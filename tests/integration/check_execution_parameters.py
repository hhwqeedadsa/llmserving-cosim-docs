"""Audit configurable single-layer ranks against real UB endpoints and events."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def audit(plan, result, run, expected, graph_id=None):
    ranks = plan['rank_ids']
    mapping = dict(zip(ranks, plan['rank_to_ub_node']))
    transfers = [t for t in result['transfers'] if graph_id is None or t['graph_id'] == graph_id]
    execution = {(n['rank_id'], n['node_id']): n for n in result['node_execution']
                 if graph_id is None or n['graph_id'] == graph_id}
    assert plan['expert_input_counts'] == expected['inputs']
    assert len(transfers) == len(plan['transfers']) == expected['writes']
    assert sum(t['bytes'] for t in transfers) == expected['bytes']
    commits = {c['task']: c for c in result['target_writes']}
    starts = {c['task']: c for c in result['wqe_starts']}
    ends = {c['task']: c for c in result['wqe_completions']}
    actual = {t['transfer_id']: t for t in transfers}
    for t in plan['transfers']:
        got = actual[t['transfer_id']]
        assert got['src_ub_node'] == mapping[t['src_rank']]
        assert got['dst_ub_node'] == mapping[t['dst_rank']]
        assert got['submitted_tasks'] == got['send_callbacks'] == got['receive_callbacks'] == 1
        assert got['committed_bytes'] == got['bytes'] == t['payload_bytes']
        assert not got['context_active'] and got['committed_segments'] == 1
        commit = commits[t['ub_task_id']]
        assert (commit['src'], commit['dst']) == (mapping[t['src_rank']], mapping[t['dst_rank']])
        assert starts[t['ub_task_id']]['node'] == ends[t['ub_task_id']]['node'] == mapping[t['src_rank']]
        assert commit['bytes'] == got['bytes']
        assert commit['time_ns'] == got['data_ready_ns'] < got['transaction_done_ns'] == got['send_done_ns']
        assert got['receive_done_ns'] == max(got['data_ready_ns'], got['receive_start_ns'])
        for side, field in [('send', 'src_rank'), ('recv', 'dst_rank')]:
            node = execution[t[field], t[side + '_node_id']]
            prefix = 'send' if side == 'send' else 'receive'
            assert node['start_ns'] == got[prefix + '_start_ns'] and node['end_ns'] == got[prefix + '_done_ns']
    assert len(execution) == sum(len(g['nodes']) for g in plan['rank_graphs'])
    for graph in plan['rank_graphs']:
        for node in graph['nodes']:
            event = execution[graph['rank_id'], node['node_id']]
            if node['kind'] == 'COMP':
                assert event['end_ns'] - event['start_ns'] == node['duration_ns']
            for dep in node['deps']:
                assert event['start_ns'] >= execution[graph['rank_id'], dep]['end_ns']
    assert result['rank_ids'] == ranks and result['rank_to_ub_node'] == plan['rank_to_ub_node']
    assert result['loaded_ranks_before_activation'] == len(ranks)
    assert result['active_wqes'] == [0] * len(ranks) and result['csv_completed_tasks'] == 0
    assert all(n <= plan['send_slots_per_rank'] for n in result['peak_wqes'])
    for state in result['rank_resources']:
        assert state['finished'] and not state['remaining_nodes']
        assert all(state[k] == 0 for k in ('cpu_slots', 'compute_slots', 'send_slots', 'pending_events', 'pending_tasks'))
    assert not (run / 'log/err.log').read_text()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    network = root / 'fixtures/m2_single_layer/network'
    system = root / 'fixtures/m1_compute/system.json'
    fixtures = root / 'fixtures/m6_execution_parameters'
    commands, checks = [], []

    def invoke(label, argv, expected=0):
        start = datetime.now(timezone.utc).isoformat()
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True)
        (output / (label + '.stdout.log')).write_text(proc.stdout)
        (output / (label + '.stderr.log')).write_text(proc.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
                             ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=proc.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        assert proc.returncode == expected, (label, proc.returncode, proc.stdout, proc.stderr)
        return proc

    expected = {'ep3': dict(inputs=[2, 3, 1], writes=15, bytes=18432),
                'ep2': dict(inputs=[0, 2], writes=3, bytes=1536),
                'idle': dict(inputs=[2, 3, 1, 0], writes=15, bytes=18432),
                'local': dict(inputs=[1], writes=0, bytes=0)}
    configs = {name: read(fixtures / (name + '.json')) for name in ('ep3', 'ep2')}
    configs['idle'] = deepcopy(configs['ep3'])
    configs['idle']['rank_ids'].append(20); configs['idle']['rank_to_ub_node'].append(1)
    configs['local'] = deepcopy(configs['ep2'])
    configs['local'].update(rank_ids=[5], rank_to_ub_node=[2], expert_placement=[5], top_k=1)
    configs['local']['tokens'] = [dict(configs['local']['tokens'][0], src_rank=5, expert_ids=[0])]
    for name, config in configs.items():
        path = output / (name + '.json'); path.write_text(json.dumps(config) + '\n')
        plan_dir = output / (name + '-plan')
        invoke(name + '-generate', [sys.executable, '-m', 'moe_ub_cosim.generate', '--input', str(path), '--output-dir', str(plan_dir)])
        plan = read(plan_dir / 'plan.json')
        reports, observations, fingerprints = [], [], []
        for mode in (('on', 'off', 'absent') if name in ('ep3', 'ep2') else ('on',)):
            label = name + '-' + mode
            config_path = output / (label + '-observation.json')
            config_path.write_text(json.dumps(dict(read(root / 'fixtures/m2_single_layer/observation.json'), record_provenance=mode == 'on')) + '\n')
            run = output / label
            invoke(label, ['timeout', '--kill-after=5s', '30s', str(build / 'output/cosim/ns3.44-cosim-layer'),
                           str(plan_dir), str(system), str(network), '-' if mode == 'absent' else str(config_path), str(run)])
            result = read(run / 'result.json'); audit(plan, result, run, expected[name]); reports.append(result)
            assert len(result['graph_completions']) == len(config['rank_ids'])
            checks.append(dict(case=label, **expected[name], graph_completions=result['graph_completions']))
            if mode != 'absent':
                observations.append({p.name: p.read_bytes() for p in (run / 'observed').iterdir()})
                fingerprints.append(read(run / 'observation-audit.json'))
                assert {r['rank_id'] for r in rows(run / 'observed/endpoint_ops.jsonl')} == set(config['rank_ids'])
            if mode == 'off':
                assert not (run / 'truth').exists()
            if mode == 'on':
                mapping = dict(zip(config['rank_ids'], config['rank_to_ub_node']))
                incoming = {t['transfer_id']: t for t in plan['transfers'] if mapping[t['dst_rank']] == 2}
                ranges = rows(run / 'truth/packet_ranges.jsonl')
                assert {p['transfer_id'] for p in ranges} == set(incoming)
                assert sum(p['task_byte_range'][1] - p['task_byte_range'][0] for p in ranges) == sum(t['payload_bytes'] for t in incoming.values())
                for row in rows(run / 'truth/transfer_registry.jsonl'):
                    assert row['src_ub_node'] == mapping[row['src_rank']] and row['dst_ub_node'] == mapping[row['dst_rank']]
                for count, truth in zip(rows(run / 'observed/port_counters.jsonl'), rows(run / 'truth/port_byte_truth.jsonl')):
                    assert count['counted_packet_bytes'] == sum(truth[k] for k in ('business_payload_bytes',
                        'application_metadata_bytes', 'data_protocol_overhead_bytes', 'control_packet_bytes', 'unattributed_bytes'))
        assert all(r == reports[0] for r in reports)
        assert all(o == observations[0] for o in observations) and all(f == fingerprints[0] for f in fingerprints)

    # Two parameterized graphs retain the same network and use fresh transfer identities.
    first = configs['ep3']; second = deepcopy(first)
    second.update(graph_id='g-m6-next', wave_id='D.w1')
    for token in second['tokens']: token['token_index'] += 1
    sequence = output / 'sequence.json'
    sequence.write_text(json.dumps(dict(schema_version='moe-ub-sequence-fixture/1', steps=[first, second])) + '\n')
    directory = output / 'sequence-plan'
    invoke('sequence-generate', [sys.executable, '-m', 'moe_ub_cosim.sequence', '--input', str(sequence), '--output-dir', str(directory)])
    run = output / 'sequence'
    invoke('sequence', ['timeout', '30s', str(build / 'output/cosim/ns3.44-cosim-sequence'), str(directory), str(system), str(network), '-', str(run)])
    result = read(run / 'result.json')
    for index in range(2):
        plan = read(run / str(index) / 'plan.json')
        audit(plan, result, run, expected['ep3'], plan['graph_id'])
    assert result['network_initializations'] == 1 and len(result['graph_completions']) == 6
    assert result['step_submissions'][1]['time_ns'] == max(e['time_ns'] for e in result['graph_completions'] if e['graph_id'] == first['graph_id'])
    assert all(r['same_network_objects'] and r['pending_tasks'] == r['pending_events'] == 0 for r in result['boundary_resources'])
    checks.append(dict(case='sequence', writes=30, bytes=36864))

    # Raw side-table mutations reach the independent C++ validator.
    bad_cases = {'duplicate-endpoint': lambda p: p['rank_to_ub_node'].__setitem__(1, p['rank_to_ub_node'][0]),
                 'missing-endpoint': lambda p: p['rank_to_ub_node'].pop(),
                 'switch-endpoint': lambda p: p['rank_to_ub_node'].__setitem__(2, 4),
                 'absent-endpoint': lambda p: p['rank_to_ub_node'].__setitem__(2, 99),
                 'endpoint-overflow': lambda p: p['rank_to_ub_node'].__setitem__(2, 2**32),
                 'rank-overflow': lambda p: p['rank_ids'].__setitem__(2, 2**31 - 1),
                 'expert-outside-group': lambda p: p['input']['expert_placement'].__setitem__(0, 99),
                 'top-k': lambda p: p['input'].update(top_k=0),
                 'duplicate-expert': lambda p: p['input']['tokens'][0].update(expert_ids=[0, 0, 1]),
                 'invalid-expert': lambda p: p['input']['tokens'][0].update(expert_ids=[0, 1, 99]),
                 'compute-overflow': lambda p: p['input']['compute_profile_ns'].update(expert_per_input=2**62),
                 'byte-overflow': lambda p: p['input'].update(hidden_dim=2**63 - 1),
                 'false-source': lambda p: p['transfers'][0]['sources'][0].update(expert_ids=[3])}
    for name, mutate in bad_cases.items():
        directory = output / ('bad-' + name); shutil.copytree(output / 'ep3-plan', directory)
        p = read(directory / 'plan.json'); mutate(p)
        p['input']['rank_to_ub_node'] = p['rank_to_ub_node']
        p['input']['rank_ids'] = p['rank_ids']
        (directory / 'plan.json').write_text(json.dumps(p) + '\n')
        run = output / ('rejected-' + name)
        proc = invoke('reject-' + name, ['timeout', '15s', str(build / 'output/cosim/ns3.44-cosim-layer'),
                      str(directory), str(system), str(network), '-', str(run)], 1)
        assert proc.stderr.strip() and not (run / 'result.json').exists() and not (run / 'log/log.log').exists()
        if name not in ('switch-endpoint', 'absent-endpoint'):
            assert not run.exists(), name
    summary = dict(passed=True, runs=checks, rejected=list(bad_cases), sources={
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in ('backend', 'python/moe_ub_cosim', 'fixtures/m6_execution_parameters')
        for p in sorted((root / folder).glob('*')) if p.is_file()})
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('PASS: parameterized real UB writes, sparse logical ranks, endpoint mapping, local/idle ranks, repeated graphs and invalid inputs')


if __name__ == '__main__':
    main()
