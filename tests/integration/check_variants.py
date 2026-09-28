"""M2.3: dedup, then chunking, then two SEND slots, each with real UB evidence."""
import argparse
from collections import defaultdict
from copy import deepcopy
import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from check_delay import check_execution, read, sha


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = build / 'output/cosim/ns3.44-cosim-layer'
    system, network = root / 'fixtures/m1_compute/system.json', root / 'fixtures/m2_single_layer/network'
    observation = read(root / 'fixtures/m2_single_layer/observation.json')
    commands, summaries, plans, configs = [], {}, {}, {}

    def invoke(label, argv):
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return result

    for name, count, total, inputs, slots in [('dedup', 12, 98304, [2, 1, 4, 1], 1),
                                             ('chunks', 42, 114688, [2, 2, 3, 1], 1),
                                             ('two_slots', 42, 114688, [2, 2, 3, 1], 2)]:
        config = root / f'fixtures/m2_variants/{name}.json'
        configs[name] = read(config)
        shutil.copyfile(config, output / (name + '-input.json'))
        generated = output / (name + '-generated')
        result = invoke(name + '-generate', [sys.executable, '-m', 'moe_ub_cosim.generate', '--input', str(config), '--output-dir', str(generated)])
        assert result.returncode == 0, result.stderr
        plan = plans[name] = read(generated / 'plan.json')
        assert plan['expert_input_counts'] == inputs and len(plan['transfers']) == count
        assert sum(t['payload_bytes'] for t in plan['transfers']) == total
        assert plan['send_slots_per_rank'] == slots
        assert len(plan['operations']) == 2 and len({t['transfer_id'] for t in plan['transfers']}) == count
        assert len({t['comm_tag'] for t in plan['transfers']}) == len({t['ub_task_id'] for t in plan['transfers']}) == count
        assert sum(len(g['nodes']) for g in plan['rank_graphs']) == 20 + 2 * count
        grouped = defaultdict(list)
        transfers = {t['transfer_id']: t for t in plan['transfers']}
        for t in plan['transfers']:
            grouped[t['kind'], t['src_rank'], t['dst_rank']].append(t)
        for key, parts in grouped.items():
            assert [p['payload_bytes'] for p in parts] == ([8192] if name == 'dedup' else [3072, 3072, 2048])
            assert [p['chunk_index'] for p in parts] == list(range(len(parts)))
            assert [p['pair_byte_range'] for p in parts] == ([[0, 8192]] if name == 'dedup' else [[0, 3072], [3072, 6144], [6144, 8192]])
            assert all(p['operation_id'] == parts[0]['operation_id'] and p['sources'] == parts[0]['sources'] for p in parts)
        if name == 'dedup':
            assert ('dispatch', 0, 1) not in grouped and ('combine', 1, 0) not in grouped
            assert grouped['dispatch', 0, 2][0]['sources'][0]['expert_ids'] == [4, 5]
            assert grouped['combine', 2, 0][0]['sources'][0]['expert_ids'] == [4, 5]
        for graph in plan['rank_graphs']:
            expert = next(n for n in graph['nodes'] if n['node_id'] == graph['phase_nodes']['expert'])
            assert len(expert['expert_inputs']) == inputs[graph['rank_id']]
            assert expert['duration_ns'] == inputs[graph['rank_id']] * 4000
        reports, evidence, observed, audits, checks = [], [], [], [], []
        for mode in ('on', 'off', 'absent'):
            label = name + '-' + mode
            obs = output / (label + '.json'); obs.write_text(json.dumps(dict(observation, record_provenance=mode == 'on')) + '\n')
            run = output / label
            result = invoke(label, ['timeout', '--kill-after=5s', '30s', str(binary), str(generated), str(system), str(network),
                                    '-' if mode == 'absent' else str(obs), str(run)])
            assert result.returncode == 0 and not result.stderr.strip(), (label, result.stdout, result.stderr)
            report = read(run / 'result.json')
            checks.append(check_execution(plan, report, run, check_rank2_ahead=False))
            assert checks[-1]['peak_sends'] == report['peak_wqes'] == [slots] * 4
            assert report['active_wqes'] == [0] * 4 and report['csv_completed_tasks'] == 0
            assert len(report['wqe_starts']) == len(report['wqe_completions']) == len(report['target_writes']) == count
            commits = {v['task']: v for v in report['target_writes']}
            wqe_ends = {v['task']: v for v in report['wqe_completions']}
            for got in report['transfers']:
                t = transfers[got['transfer_id']]
                assert got['submitted_tasks'] == 1 and got['bytes'] == got['committed_bytes'] == t['payload_bytes']
                assert commits[t['ub_task_id']]['bytes'] == t['payload_bytes']
                assert got['data_ready_ns'] == commits[t['ub_task_id']]['time_ns'] < got['transaction_done_ns']
                assert got['transaction_done_ns'] == wqe_ends[t['ub_task_id']]['time_ns']
            assert all(s['finished'] and not s['remaining_nodes'] and all(s[k] == 0 for k in
                       ('cpu_slots', 'compute_slots', 'send_slots', 'pending_events', 'pending_tasks')) for s in report['rank_resources'])
            raw = (run / 'log/log.log').read_text()
            nodes = {(g['rank_id'], n['node_id']): n for g in plan['rank_graphs'] for n in g['nodes']}
            order = defaultdict(list)
            for rank, node_id in re.findall(r'issue,sys->id=(\d+), tick=\d+, node->id=(\d+),', raw):
                n = nodes[int(rank), int(node_id)]
                if n['kind'] == 'SEND':
                    t = transfers[n['transfer_id']]
                    order[int(rank), t['kind']].append((t['dst_rank'], t['chunk_index']))
            assert all(seq == sorted(seq) for seq in order.values())
            graphs = [sha(run / f'layer.{r}.et') for r in range(4)]
            assert graphs == [sha(generated / f'layer.{r}.et') for r in range(4)]
            reports.append(report)
            evidence.append(dict(graphs=graphs, astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '', raw, flags=re.MULTILINE),
                network={str(p.relative_to(run)): sha(p) for p in (run / 'network/runlog').rglob('*') if p.is_file()}))
            if mode == 'absent':
                assert not (run / 'observed').exists() and not (run / 'truth').exists()
                continue
            observed.append({p.name: sha(p) for p in (run / 'observed').iterdir()})
            audits.append(read(run / 'observation-audit.json'))
            counters = rows(run / 'observed/port_counters.jsonl')
            assert len(counters) == 100 and sum(p['counted_packet_bytes'] for p in counters) == audits[-1]['native_tx_bytes_delta']
            if mode == 'off':
                assert not (run / 'truth').exists(); continue
            registry = {t['transfer_id']: t for t in rows(run / 'truth/transfer_registry.jsonl')}
            assert set(registry) == set(transfers)
            for tid, t in transfers.items():
                got = registry[tid]
                assert got['operation_id'] == t['operation_id'] and got['ub_task_id'] == t['ub_task_id']
                assert got['chunk_id'] == t['chunk_index'] and got['task_byte_range'] == [0, t['payload_bytes']]
                assert got['pair_byte_range'] == t['pair_byte_range'] and got['pair_payload_bytes'] == 8192
            ranges = rows(run / 'truth/packet_ranges.jsonl')
            incoming = {t['transfer_id'] for t in transfers.values() if t['dst_rank'] == 2}
            assert {p['transfer_id'] for p in ranges} == incoming
            for tid in incoming:
                cursor = 0
                for packet in sorted((p for p in ranges if p['transfer_id'] == tid), key=lambda p: p['task_byte_range'][0]):
                    a, b = packet['task_byte_range']; c, d = packet['packet_byte_range']
                    assert a == cursor and b - a == d - c and packet['operation_id'] == transfers[tid]['operation_id']
                    cursor = b
                assert cursor == transfers[tid]['payload_bytes']
            bodies = rows(run / 'truth/port_byte_truth.jsonl')
            assert sum(b['business_payload_bytes'] for b in bodies) == 24576
            for counter, body in zip(counters, bodies):
                assert counter['counted_packet_bytes'] == sum(body[k] for k in
                    ('business_payload_bytes', 'application_metadata_bytes', 'data_protocol_overhead_bytes', 'control_packet_bytes', 'unattributed_bytes'))
                matching = [p for p in ranges if body['window_start_sim_ns'] <= p['sim_time_ns'] < body['window_end_sim_ns']]
                assert body['business_payload_bytes'] == sum(p['task_byte_range'][1] - p['task_byte_range'][0] for p in matching)
            assert len(rows(run / 'truth/operation_registry.jsonl')) == 2
        assert reports[0] == reports[1] == reports[2] and evidence[0] == evidence[1] == evidence[2]
        assert observed[0] == observed[1] and audits[0] == audits[1]
        summaries[name] = dict(transfers=count, business_bytes=total, expert_inputs=inputs, send_slots=slots,
                              execution=checks[0], port_audit=audits[0], identical_native_files=len(evidence[0]['network']))
        print(f'PASS: {name}, {count} real writes, {total} bytes, peak SEND/WQE {[slots] * 4}, provenance on/off/absent')
    # Explicitly prove each control/treatment relationship uses only its intended change.
    baseline = read(root / 'fixtures/m2_single_layer/layer.json')
    expected = deepcopy(baseline); expected['tokens'][0]['expert_ids'] = [4, 5]; assert configs['dedup'] == expected
    expected = deepcopy(baseline); expected['chunk_bytes'] = 3072; assert configs['chunks'] == expected
    expected = deepcopy(configs['chunks']); expected['send_slots_per_rank'] = 2; assert configs['two_slots'] == expected
    assert plans['chunks']['rank_graphs'] == plans['two_slots']['rank_graphs']
    assert plans['chunks']['transfers'] == plans['two_slots']['transfers']
    assert all(sha(output / 'chunks-generated' / f'layer.{r}.et') == sha(output / 'two_slots-generated' / f'layer.{r}.et') for r in range(4))

    corruptions = [('chunk-index', lambda p: p['transfers'][1].update(chunk_index=0)),
                   ('range-gap', lambda p: p['transfers'][1].update(pair_byte_range=[3073, 6145])),
                   ('range-overlap', lambda p: p['transfers'][1].update(pair_byte_range=[3071, 6143])),
                   ('tail', lambda p: p['transfers'][2].update(payload_bytes=3072, business_payload_bytes=3072, task_byte_range=[0, 3072])),
                   ('pair-size', lambda p: p['transfers'][0].update(pair_payload_bytes=8193)),
                   ('missing-source', lambda p: p['transfers'][1].update(sources=[])),
                   ('slot-zero', lambda p: p.update(send_slots_per_rank=0)),
                   ('slot-high', lambda p: p.update(send_slots_per_rank=3)),
                   ('slot-mismatch', lambda p: p.update(send_slots_per_rank=2)),
                   ('oversize-chunk', lambda p: p['input'].update(chunk_bytes=2**32)),
                   ('partial-expert', lambda p: p['rank_graphs'][2]['nodes'][p['rank_graphs'][2]['phase_nodes']['expert']-1]['deps'].pop())]
    for label, mutate in corruptions:
        bad = output / ('bad-' + label); shutil.copytree(output / 'chunks-generated', bad)
        plan = deepcopy(plans['chunks']); mutate(plan); (bad / 'plan.json').write_text(json.dumps(plan) + '\n')
        run = output / ('reject-' + label)
        result = invoke('bad-' + label, ['timeout', '--kill-after=5s', '30s', str(binary), str(bad), str(system), str(network), '-', str(run)])
        assert result.returncode == 1 and 'Layer plan:' in result.stderr and not run.exists(), (label, result.stderr)
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, cases=summaries, real_ub_runs=9,
        rejected_inputs=len(corruptions), binary_sha256=sha(binary)), indent=2) + '\n')
    print('PASS: single-variable inputs, identical one/two-slot graphs, chunk-range provenance and atomic invalid-plan rejection')


if __name__ == '__main__':
    main()
