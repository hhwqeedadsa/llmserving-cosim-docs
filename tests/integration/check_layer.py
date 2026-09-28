"""Run the fixed four-rank layer and audit its real ASTRA/UB evidence."""
import argparse
import collections
from copy import deepcopy
import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = build / 'output/cosim/ns3.44-cosim-layer'
    fixture = root / 'fixtures/m2_single_layer/layer.json'
    system = root / 'fixtures/m1_compute/system.json'
    network = root / 'fixtures/m2_single_layer/network'
    observation = read(root / 'fixtures/m2_single_layer/observation.json')
    commands = []

    def invoke(label, command):
        started = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=command, cwd=str(root), started_utc=started,
                             finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return result

    for label in ('generated', 'generated-again'):
        result = invoke(label, [sys.executable, '-m', 'moe_ub_cosim.generate', '--input', str(fixture),
                                '--output-dir', str(output / label)])
        assert result.returncode == 0, result.stderr
    generated = output / 'generated'
    for name in ['plan.json'] + [f'layer.{rank}.et' for rank in range(4)]:
        assert sha(generated / name) == sha(output / 'generated-again' / name), name
    plan = read(generated / 'plan.json')
    transfer_map = {t['transfer_id']: t for t in plan['transfers']}
    tasks = {t['ub_task_id']: t for t in plan['transfers']}
    nodes = {(g['rank_id'], n['node_id']): n for g in plan['rank_graphs'] for n in g['nodes']}
    expected = [[0, 8192, 8192, 0], [8192, 0, 8192, 0], [0, 0, 0, 8192], [8192, 8192, 0, 0]]
    assert plan['expert_input_counts'] == [2, 2, 3, 1]
    assert len(transfer_map) == len(tasks) == 14 and len(nodes) == 48
    for kind in ('dispatch', 'combine'):
        matrix = [[0] * 4 for _ in range(4)]
        for t in transfer_map.values():
            if t['kind'] == kind:
                matrix[t['src_rank']][t['dst_rank']] += t['payload_bytes']
        assert matrix == (expected if kind == 'dispatch' else [list(v) for v in zip(*expected)])
    library_names = ('applications', 'bridge', 'config-store', 'core', 'internet', 'network',
                     'point-to-point', 'stats', 'traffic-control', 'unified-bus')
    libraries = {build / 'output/lib' / f'libns3.44-{name}.so' for name in library_names}
    results, audits, observed, evidence = [], [], [], []
    for mode in ('on', 'off', 'absent'):
        config = output / f'observation-{mode}.json'
        config.write_text(json.dumps(dict(observation, record_provenance=mode == 'on')) + '\n')
        run = output / mode
        result = invoke(mode, ['timeout', '--kill-after=5s', '30s', str(binary), str(generated), str(system),
                               str(network), '-' if mode == 'absent' else str(config), str(run)])
        assert result.returncode == 0 and not result.stderr.strip(), (mode, result.stdout, result.stderr)
        report = read(run / 'result.json')
        assert report['calibration_status'] == 'unvalidated_reference'
        assert report['loaded_ranks_before_activation'] == 4
        assert report['activation_order'] == [dict(rank_id=r, time_ns=0) for r in range(4)]
        assert report['csv_completed_tasks'] == 0 and report['later_event_executed'] is True
        assert report['simulation_end_ns'] == report['test_stop_ns'] == 1000000
        assert report['initial_tp_counts'] == [3] * 4
        assert report['peak_wqes'] == [1] * 4 and report['active_wqes'] == [0] * 4
        assert len(report['transfers']) == len(report['wqe_starts']) == len(report['wqe_completions']) == 14
        assert len(report['target_writes']) == 14
        assert sum(t['committed_bytes'] for t in report['transfers']) == 114688
        assert sum(t['bytes'] for t in report['target_writes']) == 114688
        actual = {t['transfer_id']: t for t in report['transfers']}
        commits = {t['task']: t for t in report['target_writes']}
        assert set(commits) == set(tasks)
        execution = {(n['rank_id'], n['node_id']): n for n in report['node_execution']}
        assert set(execution) == set(nodes)
        for tid, t in transfer_map.items():
            got, commit = actual[tid], commits[t['ub_task_id']]
            assert got['submitted_tasks'] == got['send_callbacks'] == got['receive_callbacks'] == 1
            assert got['committed_bytes'] == got['bytes'] == commit['bytes'] == 8192
            assert got['committed_segments'] == 1 and got['context_active'] is False
            assert commit['src'] == t['src_rank'] and commit['dst'] == t['dst_rank']
            assert got['data_ready_ns'] == commit['time_ns'] < got['transaction_done_ns'] == got['send_done_ns']
            assert got['receive_done_ns'] == max(got['receive_start_ns'], got['data_ready_ns'])
            for side, rank_field in [('send', 'src_rank'), ('recv', 'dst_rank')]:
                event = execution[t[rank_field], t[side + '_node_id']]
                prefix = 'send' if side == 'send' else 'receive'
                assert event['start_ns'] == got[prefix + '_start_ns']
                assert event['end_ns'] == got[prefix + '_done_ns']
        for key, node in nodes.items():
            event = execution[key]
            assert event['end_ns'] >= event['start_ns']
            if node['kind'] == 'COMP':
                assert event['end_ns'] - event['start_ns'] == node['duration_ns']
            for dep in node['deps']:
                assert event['start_ns'] >= execution[key[0], dep]['end_ns']
        ends = {v['rank_id']: v['time_ns'] for v in report['graph_completions']}
        assert len(ends) == 4
        for graph, state in zip(plan['rank_graphs'], report['rank_resources']):
            rank, phase = graph['rank_id'], graph['phase_nodes']
            assert state == dict(rank_id=rank, finished=True, remaining_nodes=False,
                                 cpu_slots=0, compute_slots=0, send_slots=0, pending_events=0, pending_tasks=0, tp_count=3)
            assert ends[rank] == execution[rank, phase['next_layer']]['end_ns']
            for entry, exit_node in [('prepare', 'expert'), ('local_merge', 'finish')]:
                dep_ids = nodes[rank, phase[exit_node]]['deps']
                assert execution[rank, phase[exit_node]]['start_ns'] == max(execution[rank, dep]['end_ns'] for dep in dep_ids)
        raw_astra = (run / 'log/log.log').read_text()
        log_events = re.findall(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+),', raw_astra)
        assert len(log_events) == 96
        active, peak = [0] * 4, [0] * 4
        receive_during_send = 0
        ordering = collections.defaultdict(list)
        seen = set()
        for event, rank, time, node_id in log_events:
            rank, time, node_id = int(rank), int(time), int(node_id)
            node = nodes[rank, node_id]
            assert (event, rank, node_id) not in seen
            seen.add((event, rank, node_id))
            assert time == execution[rank, node_id]['start_ns' if event == 'issue' else 'end_ns']
            if node['kind'] == 'RECV' and event == 'issue' and active[rank] == 1:
                receive_during_send += 1
            if node['kind'] == 'SEND':
                active[rank] += 1 if event == 'issue' else -1
                assert 0 <= active[rank] <= 1
                peak[rank] = max(peak[rank], active[rank])
                if event == 'issue':
                    t = transfer_map[node['transfer_id']]
                    ordering[rank, t['kind']].append(t['dst_rank'])
        assert active == [0] * 4 and peak == [1] * 4
        assert receive_during_send > 0, 'RECV must still issue while the SEND slot is occupied'
        assert all(destinations == sorted(destinations) for destinations in ordering.values())
        loaded = {Path(v).resolve() for v in report['loaded_libraries'] if Path(v).name.startswith('libns3')}
        assert loaded == libraries
        assert not (run / 'log/err.log').read_text()
        results.append(report)
        evidence.append(dict(network={str(p.relative_to(run)): sha(p) for p in (run / 'network/runlog').rglob('*') if p.is_file()},
                             astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '', raw_astra, flags=re.MULTILINE),
                             graphs=[sha(run / f'layer.{r}.et') for r in range(4)]))
        if mode == 'absent':
            assert not (run / 'observed').exists() and not (run / 'truth').exists()
            continue
        ports = lines(run / 'observed/port_counters.jsonl')
        assert len(ports) == 100 and any(p['counted_packet_bytes'] == 0 for p in ports)
        native = (run / 'network/runlog/PortTrace_node_4_port_2.tr').read_text()
        starts = [(int(Decimal(t) * 1000), int(size)) for t, size in re.findall(
            r'\[([\d.]+)us\] Port Tx, port ID: 2 PacketSize: (\d+)', native)]
        for index, row in enumerate(ports):
            sizes = [size for time, size in starts if index * 1000 <= time < (index + 1) * 1000]
            assert row == dict(switch_id=4, port_id=2, direction='egress', local_window_start_ns=index * 1000,
                               local_window_end_ns=(index + 1) * 1000, counted_packets=len(sizes), counted_packet_bytes=sum(sizes))
        endpoints = lines(run / 'observed/endpoint_ops.jsonl')
        expected_endpoints = []
        for graph in plan['rank_graphs']:
            rank, p = graph['rank_id'], graph['phase_nodes']
            expected_endpoints.extend([
                dict(rank_id=rank, local_record_id=1, kind='dispatch',
                     start_local_ns=execution[rank, p['prepare']]['end_ns'], end_local_ns=execution[rank, p['expert']]['start_ns']),
                dict(rank_id=rank, local_record_id=2, kind='combine',
                     start_local_ns=execution[rank, p['local_merge']]['end_ns'], end_local_ns=execution[rank, p['finish']]['end_ns'])])
        assert endpoints == expected_endpoints
        audits.append(read(run / 'observation-audit.json'))
        assert audits[-1]['native_tx_bytes_delta'] == sum(p['counted_packet_bytes'] for p in ports) == sum(size for _, size in starts)
        observed.append({p.name: sha(p) for p in (run / 'observed').iterdir()})
        if mode == 'off':
            assert not (run / 'truth').exists()
            continue
        registry = lines(run / 'truth/transfer_registry.jsonl')
        operations = lines(run / 'truth/operation_registry.jsonl')
        assert len(registry) == 14 and len(operations) == 2
        for op in operations:
            assert op['endpoint_records'] == [dict(rank_id=r, local_record_id=1 if op['kind'] == 'dispatch' else 2) for r in range(4)]
        ranges = lines(run / 'truth/packet_ranges.jsonl')
        bodies = lines(run / 'truth/port_byte_truth.jsonl')
        incoming = {t['transfer_id'] for t in transfer_map.values() if t['dst_rank'] == 2}
        assert len(incoming) == 3 and {r['transfer_id'] for r in ranges} == incoming and len(ranges) == 6
        for tid in incoming:
            cursor = 0
            for row in sorted((r for r in ranges if r['transfer_id'] == tid), key=lambda r: r['task_byte_range'][0]):
                t = transfer_map[tid]
                assert row['ub_task_id'] == t['ub_task_id'] and row['operation_id'] == t['operation_id']
                start, end = row['task_byte_range']; a, b = row['packet_byte_range']
                assert start == cursor and end - start == b - a and b == row['counted_packet_bytes']
                cursor = end
            assert cursor == 8192
        assert sum(b['business_payload_bytes'] for b in bodies) == 24576
        assert sum(b['control_packet_bytes'] for b in bodies) > 0
        for row, body in zip(ports, bodies):
            assert row['counted_packet_bytes'] == sum(body[k] for k in ('business_payload_bytes',
                'application_metadata_bytes', 'data_protocol_overhead_bytes', 'control_packet_bytes', 'unattributed_bytes'))
            matching = [r for r in ranges if row['local_window_start_ns'] <= r['sim_time_ns'] < row['local_window_end_ns']]
            assert body['business_payload_bytes'] == sum(r['task_byte_range'][1] - r['task_byte_range'][0] for r in matching)
    assert results[0] == results[1] == results[2]
    assert evidence[0] == evidence[1] == evidence[2]
    assert audits[0] == audits[1] and observed[0] == observed[1]
    print('PASS: fourteen real writes, eight expert inputs, 48 ASTRA nodes, one SEND per rank, provenance on/off/absent')

    # Corrupt a later rank as well: no earlier rank may execute before validation ends.
    bad_plans = [('duplicate-tag', lambda p: p['transfers'][1].update(comm_tag=p['transfers'][0]['comm_tag'])),
                 ('duplicate-task', lambda p: p['transfers'][1].update(ub_task_id=p['transfers'][0]['ub_task_id'])),
                 ('oversize', lambda p: p['transfers'][0].update(payload_bytes=2**32)),
                 ('missing-recv', lambda p: p['transfers'][0].update(recv_node_id=999)),
                 ('nonlocal-dependency', lambda p: p['rank_graphs'][3]['nodes'][1].update(deps=[999])),
                 ('broken-phase', lambda p: p['rank_graphs'][3]['phase_nodes'].update(expert=999)),
                 ('payload-mismatch', lambda p: p['transfers'][0].update(payload_bytes=8191)),
                 ('graph-mismatch', lambda p: p['rank_graphs'][3]['nodes'][0].update(duration_ns=5001))]
    bad_plans.append(('zero-compute', lambda p: p['rank_graphs'][3]['nodes'][0].update(duration_ns=0)))
    for label, change in bad_plans:
        bad = output / ('bad-' + label); shutil.copytree(generated, bad)
        data = deepcopy(plan); change(data)
        (bad / 'plan.json').write_text(json.dumps(data) + '\n')
        result = invoke(label, [str(binary), str(bad), str(system), str(network), '-', str(output / ('run-' + label))])
        assert result.returncode == 1 and 'Layer plan:' in result.stderr, (label, result.stderr)
        assert not (output / ('run-' + label)).exists()
    for label in ('missing-last-rank', 'truncated-last-rank', 'extra-node'):
        bad = output / label; shutil.copytree(generated, bad)
        last = bad / 'layer.3.et'
        if label == 'missing-last-rank': last.unlink()
        elif label == 'truncated-last-rank': last.write_bytes(last.read_bytes()[:-1])
        else: last.write_bytes(last.read_bytes() + b'\x02\x08\x01')
        result = invoke(label, [str(binary), str(bad), str(system), str(network), '-', str(output / ('run-' + label))])
        assert result.returncode == 1 and 'Layer plan:' in result.stderr, (label, result.stderr)
        assert not (output / ('run-' + label)).exists()
    before = {p: sha(p) for p in (output / 'on').rglob('*') if p.is_file()}
    result = invoke('overwrite', [str(binary), str(generated), str(system), str(network), '-', str(output / 'on')])
    assert result.returncode == 1 and 'must be new' in result.stderr
    assert all(sha(p) == digest for p, digest in before.items())
    recipe = invoke('layer-commands', ['ninja', '-C', str(build), '-t', 'commands', 'cosim-layer'])
    assert recipe.returncode == 0
    forbidden = ('network_frontend/', 'extern/network_backend/')
    assert not any(v in recipe.stdout for v in forbidden)
    sources = []
    for line in recipe.stdout.splitlines():
        words = shlex.split(line)
        if '-c' in words:
            path = Path(words[words.index('-c') + 1]).resolve()
            assert any(path.is_relative_to(root / area) for area in ('backend', 'third_party/ns-3-ub', 'third_party/LLMServingSim/astra-sim'))
            sources.append(dict(path=str(path), sha256=sha(path), command=line))
    counts = collections.Counter(s['path'] for s in sources)
    assert all(n == 1 for n in counts.values()) and counts[str(root / 'backend/cosim_layer.cc')] == 1
    link_map = (build / 'cosim-m2/layer-link.map').read_text()
    assert not any(v in link_map for v in forbidden)
    members = re.findall(r'libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)', link_map)
    assert len(members) == len(set(members)) == 52
    assert {(build / path).resolve() for path in re.findall(r'\bLOAD\s+(\S*libns3[^\s]*)', link_map)} == libraries
    (output / 'compiled-sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, simulation_runs=3, rejected_inputs=13,
        transfers=14, business_bytes=114688, expert_inputs=[2, 2, 3, 1], astra_nodes=48,
        graph_completions=results[0]['graph_completions'], port_audit=audits[0],
        identical_native_files=len(evidence[0]['network']), binary_sha256=sha(binary),
        link_map_sha256=sha(build / 'cosim-m2/layer-link.map')), indent=2) + '\n')
    print('PASS: atomic graph rejection, deterministic export, old output preservation, 52 ASTRA objects / ten UB libraries')


if __name__ == '__main__':
    main()
