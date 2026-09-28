"""Two real UB graphs in one process/network, plus isolated lifecycle faults."""
import argparse
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import check_execution, read, sha
from check_variants import rows
from moe_ub_cosim.generate import export_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = build / 'output/cosim/ns3.44-cosim-sequence'
    fault_binary = build / 'output/cosim/ns3.44-cosim-lifecycle-test'
    system, network = root / 'fixtures/m1_compute/system.json', root / 'fixtures/m2_single_layer/network'
    commands = []

    def invoke(label, argv):
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return result

    archive = build / 'cosim-m0/lib/libAstraSim.a'
    result = invoke('archive-members', ['ar', 't', str(archive)])
    assert result.returncode == 0
    members = result.stdout.splitlines()
    assert len(members) == 52
    for target in ('cosim-sequence', 'cosim-lifecycle-test'):
        result = invoke(target + '-commands', ['ninja', '-C', str(build), '-t', 'commands', target])
        assert result.returncode == 0
        link = (build / 'cosim-m2' / (target + '-link.map')).read_text()
        assert not any(term in link + result.stdout for term in ('extern/network_backend/', 'network_frontend/'))
        linked = re.findall(r'libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)', link)
        assert Counter(linked) == Counter(members)
    modules = ('applications', 'bridge', 'config-store', 'core', 'internet', 'network',
               'point-to-point', 'stats', 'traffic-control', 'unified-bus')
    libraries = {build / 'output/lib' / f'libns3.44-{name}.so' for name in modules}
    generated = output / 'generated'
    fixture = root / 'fixtures/m2_sequence/two_steps.json'
    shutil.copyfile(fixture, output / 'input.json')
    result = invoke('generate', [sys.executable, '-m', 'moe_ub_cosim.sequence', '--input', str(fixture), '--output-dir', str(generated)])
    assert result.returncode == 0, result.stderr
    plans = [read(generated / str(i) / 'plan.json') for i in range(2)]
    for field in ('transfer_id', 'ub_task_id', 'comm_tag'):
        values = [t[field] for p in plans for t in p['transfers']]
        assert len(set(values)) == len(values) == 28
    assert len({op['operation_id'] for p in plans for op in p['operations']}) == 4
    assert [[n['node_id'] for n in g['nodes']] for g in plans[0]['rank_graphs']] == [
        [n['node_id'] for n in g['nodes']] for g in plans[1]['rank_graphs']]
    reports, evidence, audits, observed, executions = [], [], [], [], []
    observation = read(root / 'fixtures/m2_single_layer/observation.json')
    for mode in ('on', 'off', 'absent', 'repeat', 'stale'):
        obs = output / (mode + '.json')
        obs.write_text(json.dumps(dict(observation, record_provenance=mode in ('on', 'repeat', 'stale'))) + '\n')
        run = output / mode
        result = invoke(mode, ['timeout', '--kill-after=5s', '30s', str(fault_binary if mode == 'stale' else binary),
            str(generated), str(system), str(network), '-' if mode == 'absent' else str(obs), str(run)])
        assert result.returncode == 0 and not result.stderr.strip(), (mode, result.stdout, result.stderr)
        report = read(run / 'result.json')
        assert {Path(p).resolve() for p in report['loaded_libraries'] if Path(p).name.startswith('libns3')} == libraries
        assert report['network_initializations'] == 1 and report['simulation_end_ns'] == report['test_stop_ns']
        assert report['later_event_executed'] and report['loaded_ranks_before_activation'] == 4
        assert len(report['graph_loads']) == len(report['activation_order']) == len(report['graph_completions']) == 8
        assert len(report['step_submissions']) == 2
        times = [v['time_ns'] for v in report['step_submissions']]
        assert times[0] == 0 and times[1] == max(v['time_ns'] for v in report['graph_completions'] if v['graph_id'] == plans[0]['graph_id'])
        assert times[1] > min(v['time_ns'] for v in report['graph_completions'] if v['graph_id'] == plans[0]['graph_id'])
        assert len(report['boundary_resources']) == 4 and all(v['same_network_objects'] and v['iteration'] == 0 and
            v['pending_events'] == v['pending_tasks'] == 0 and v['time_ns'] == times[1] for v in report['boundary_resources'])
        checks = []
        for index, plan in enumerate(plans):
            graph = plan['graph_id']; local_plan = deepcopy(plan); local_plan['ready_ns'] = times[index]
            assert read(run / str(index) / 'plan.json')['ready_ns'] == times[index]
            for rank in range(4):
                assert sha(run / str(index) / f'layer.{rank}.et') == sha(generated / str(index) / f'layer.{rank}.et')
            local = dict(loaded_ranks_before_activation=4)
            for key in ('activation_order', 'graph_completions', 'node_execution', 'transfers'):
                local[key] = [{k: v for k, v in e.items() if k != 'graph_id'} for e in report[key] if e['graph_id'] == graph]
            assert [r for r in report['graph_loads'] if r['graph_id'] == graph] == [
                dict(graph_id=graph, rank_id=r, time_ns=times[index]) for r in range(4)]
            assert min(n['start_ns'] for n in local['node_execution']) == times[index]
            checks.append(check_execution(local_plan, local, run, check_rank2_ahead=False, graph_id=graph))
        executions.append(checks)
        assert len(report['transfers']) == len(report['wqe_starts']) == len(report['wqe_completions']) == len(report['target_writes']) == 28
        assert sum(t['bytes'] for t in report['transfers']) == 229376
        commits = {v['task']: v for v in report['target_writes']}
        ends = {v['task']: v for v in report['wqe_completions']}
        for transfer in report['transfers']:
            assert transfer['submitted_tasks'] == 1 and transfer['committed_segments'] == 1
            assert transfer['bytes'] == transfer['committed_bytes'] == commits[transfer['ub_task_id']]['bytes'] == 8192
            assert transfer['data_ready_ns'] == commits[transfer['ub_task_id']]['time_ns'] < transfer['transaction_done_ns']
            assert transfer['send_done_ns'] == ends[transfer['ub_task_id']]['time_ns']
        assert report['active_wqes'] == [0] * 4 and report['peak_wqes'] == [1] * 4 and report['csv_completed_tasks'] == 0
        assert all(v['finished'] and not v['remaining_nodes'] and v['iteration'] == 1 and
            all(v[k] == 0 for k in ('cpu_slots', 'compute_slots', 'send_slots', 'pending_events', 'pending_tasks')) for v in report['rank_resources'])
        raw = (run / 'log/log.log').read_text()
        lifecycle = re.findall(r'graph_(load|activate),rank=(\d+), tick=(\d+), graph_id=([^\s]+)', raw)
        assert lifecycle == [(event, str(rank), str(times[i]), plans[i]['graph_id'])
                             for i in range(2) for event in ('load', 'activate') for rank in range(4)]
        if mode == 'stale':
            rejection_keys = ('busy_rejection', 'activation_rejection', 'atomic_rejection', 'reuse_rejection', 'unactivated_rejection')
            assert all(report[k] for k in rejection_keys)
            stale = re.findall(r'stale_callback,graph_id=([^,]+), active_graph_id=([^,]+), rank=(\d+), node=(\d+), comm_tag=(\d+)', raw)
            # Rank 0 has one COMP and four Dispatch SEND/RECV contexts.
            assert len(stale) == 5 and sum(v['stale_callbacks'] for v in report['rank_resources']) == 5
            assert all(old == plans[0]['graph_id'] and new == plans[1]['graph_id'] and rank == '0' for old, new, rank, _, _ in stale)
            assert len({node for _, _, _, node, _ in stale}) == 5
            for key in rejection_keys:
                report.pop(key)
            for rank in report['rank_resources']:
                rank['stale_callbacks'] = 0
            raw = '\n'.join(line for line in raw.splitlines() if 'stale_callback,' not in line) + '\n'
        else:
            assert all(v['stale_callbacks'] == 0 for v in report['rank_resources']) and 'stale_callback,' not in raw
        reports.append(report)
        evidence.append(dict(astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '', raw, flags=re.MULTILINE),
            native={str(p.relative_to(run)): sha(p) for p in (run / 'network/runlog').rglob('*') if p.is_file()}))
        if mode == 'absent':
            assert not (run / 'truth').exists() and not (run / 'observed').exists(); continue
        observed.append({p.name: sha(p) for p in (run / 'observed').iterdir()})
        audits.append(read(run / 'observation-audit.json'))
        counters = rows(run / 'observed/port_counters.jsonl')
        endpoint = rows(run / 'observed/endpoint_ops.jsonl')
        native = (run / 'network/runlog/PortTrace_node_4_port_2.tr').read_text()
        starts = [(int(Decimal(t) * 1000), int(size)) for t, size in re.findall(
            r'\[([\d.]+)us\] Port Tx, port ID: 2 PacketSize: (\d+)', native)]
        for row in counters:
            sizes = [size for time, size in starts if row['local_window_start_ns'] <= time < row['local_window_end_ns']]
            assert row['counted_packets'] == len(sizes) and row['counted_packet_bytes'] == sum(sizes)
        assert len(counters) == 100 and len(endpoint) == len({(e['rank_id'], e['local_record_id']) for e in endpoint}) == 16
        assert sum(c['counted_packet_bytes'] for c in counters) == audits[-1]['native_tx_bytes_delta']
        hidden = ('graph_id', 'operation_id', 'transfer_id', 'comm_tag', 'request_id', 'token_index')
        assert all(all(k not in row for k in hidden) for row in endpoint + counters)
        if mode == 'off':
            assert not (run / 'truth').exists(); continue
        registry = rows(run / 'truth/transfer_registry.jsonl')
        operations = rows(run / 'truth/operation_registry.jsonl')
        assert len(registry) == 28 and len(operations) == 4
        assert {o['graph_id'] for o in operations} == {p['graph_id'] for p in plans}
        for i, plan in enumerate(plans):
            assert {r['local_record_id'] for o in operations if o['graph_id'] == plan['graph_id'] for r in o['endpoint_records']} == {1 + i*2, 2 + i*2}
        ranges = rows(run / 'truth/packet_ranges.jsonl')
        transfers = {t['transfer_id']: t for plan in plans for t in plan['transfers']}
        incoming = {t['transfer_id'] for t in transfers.values() if t['dst_rank'] == 2}
        assert {p['transfer_id'] for p in ranges} == incoming
        for tid in incoming:
            cursor = 0
            for packet in sorted((p for p in ranges if p['transfer_id'] == tid), key=lambda p: p['task_byte_range'][0]):
                start, end = packet['task_byte_range']
                assert start == cursor and packet['operation_id'] == transfers[tid]['operation_id']
                cursor = end
            assert cursor == 8192
        bodies = rows(run / 'truth/port_byte_truth.jsonl')
        assert sum(b['business_payload_bytes'] for b in bodies) == 49152
        for counter, body in zip(counters, bodies):
            assert counter['counted_packet_bytes'] == sum(body[k] for k in
                ('business_payload_bytes', 'application_metadata_bytes', 'data_protocol_overhead_bytes', 'control_packet_bytes', 'unattributed_bytes'))
            packets = [p for p in ranges if body['window_start_sim_ns'] <= p['sim_time_ns'] < body['window_end_sim_ns']]
            assert body['business_payload_bytes'] == sum(p['task_byte_range'][1] - p['task_byte_range'][0] for p in packets)
    assert all(report == reports[0] for report in reports)
    assert all(e == evidence[0] for e in evidence) and all(o == observed[0] for o in observed) and all(a == audits[0] for a in audits)
    # A malformed final rank rejects the whole set before a network/run exists.
    bad = []
    mutations = {
        'graph-id': lambda p: p.update(graph_id=plans[0]['graph_id']),
        'operation-id': lambda p: p['operations'][0].update(operation_id=plans[0]['operations'][0]['operation_id']),
        'transfer-id': lambda p: p['transfers'][0].update(transfer_id=plans[0]['transfers'][0]['transfer_id']),
        'task-id': lambda p: p['transfers'][0].update(ub_task_id=plans[0]['transfers'][0]['ub_task_id']),
        'tag': lambda p: p['transfers'][0].update(comm_tag=plans[0]['transfers'][0]['comm_tag']),
        'tag-zero': lambda p: p['transfers'][0].update(comm_tag=0),
        'tag-overflow': lambda p: p['transfers'][0].update(comm_tag=2**31),
        'next-step': lambda p: p['input']['tokens'][0].update(token_index=0),
        'future-ready': lambda p: p.update(ready_ns=1),
    }
    for name in list(mutations) + ['missing-last-rank', 'malformed-last-rank', 'extra-step']:
        folder = output / ('bad-' + name); shutil.copytree(generated, folder)
        if name in mutations:
            p = read(folder / '1/plan.json'); mutations[name](p)
            if name == 'graph-id':
                p['input']['graph_id'] = p['graph_id']
            elif name == 'operation-id':
                old = plans[1]['operations'][0]['operation_id']
                for transfer in p['transfers']:
                    if transfer['operation_id'] == old:
                        transfer['operation_id'] = p['operations'][0]['operation_id']
            elif name == 'transfer-id':
                old = plans[1]['transfers'][0]['transfer_id']
                for graph in p['rank_graphs']:
                    for node in graph['nodes']:
                        if node.get('transfer_id') == old:
                            node['transfer_id'] = p['transfers'][0]['transfer_id']
            if name in ('graph-id', 'operation-id', 'transfer-id', 'task-id', 'tag'):
                valid = output / ('individually-valid-' + name)
                export_plan(p, valid)
                for source in valid.glob('layer.*.et'):
                    shutil.copyfile(source, folder / '1' / source.name)
            (folder / '1/plan.json').write_text(json.dumps(p) + '\n')
        elif name == 'missing-last-rank':
            (folder / '1/layer.3.et').unlink()
        elif name == 'malformed-last-rank':
            (folder / '1/layer.3.et').write_bytes(b'\x80')
        else:
            (folder / 'sequence.json').write_text(json.dumps(dict(schema_version='moe-ub-sequence/1', steps=['0','1','2'])))
        run = output / ('rejected-' + name)
        result = invoke('bad-' + name, ['timeout', '15s', str(binary), str(folder), str(system), str(network), '-', str(run)])
        assert result.returncode == 1 and result.stderr.strip() and not run.exists(), (name, result.returncode, result.stderr)
        if name in ('graph-id', 'operation-id', 'transfer-id', 'task-id', 'tag'):
            assert 'Graph sequence:' in result.stderr and 'reused' in result.stderr, (name, result.stderr)
        bad.append(name)
    result = invoke('overwrite', [str(binary), str(generated), str(system), str(network), '-', str(output / 'on')])
    assert result.returncode == 1 and 'Run directory must be new' in result.stderr
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, actual_runs=5, rejected=bad,
        execution=executions[0], port_audit=audits[0], identical_native_files=len(evidence[0]['native']),
        astra_core_objects=len(members), ub_ns3_libraries=len(libraries),
        binaries={str(p.relative_to(root)): sha(p) for p in (binary, fault_binary)},
        astra_patch_sources={name: sha(root / 'third_party/LLMServingSim/astra-sim/astra-sim' / name) for name in
            ('workload/Workload.cc', 'workload/Workload.hh', 'system/WorkloadLayerHandlerData.hh')},
        sources={str(p.relative_to(root)): sha(p) for p in sorted((root / 'backend').glob('*')) if p.is_file()}), indent=2) + '\n')
    print('PASS: two graphs, 28 actual UB writes, 229376 bytes; on/off/absent/repeat/stale identical; bad sets rejected')


if __name__ == '__main__':
    main()
