"""Model budgets, reservation lifecycle and sparse endpoint mapping with real UB."""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from check_service_boundaries import check_sparse_graph


def read(path):
    return json.loads(path.read_text())


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, out = args.build_dir.resolve(), args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = root/'fixtures/m6_service_model'
    network = root/'fixtures/m2_single_layer/network'
    system = root/'fixtures/m1_compute/system.json'
    binary = build/'output/cosim/ns3.44-cosim-service'
    commands, cases, failures = [], {}, {}

    def invoke(label, argv, expected=0, stdin=None):
        start = datetime.now(timezone.utc).isoformat()
        argv = [str(v) for v in argv]
        process = subprocess.run(argv, cwd=root, input=stdin, capture_output=True, text=True, timeout=90)
        for stream in ('stdout', 'stderr'):
            (out/f'{label}.{stream}.log').write_text(getattr(process, stream))
        if stdin is not None:
            (out/f'{label}.stdin.log').write_text(stdin)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            ended_utc=datetime.now(timezone.utc).isoformat(), exit_code=process.returncode))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert process.returncode == expected, (label, process.returncode, process.stderr)
        return process

    def command(label, config, layer, mode=None):
        cp, lp = out/f'{label}.input.json', out/f'{label}.layer.json'
        cp.write_text(json.dumps(config, indent=2)+'\n'); lp.write_text(json.dumps(layer, indent=2)+'\n')
        argv = [sys.executable, '-m', 'moe_ub_cosim.service', '--input', cp, '--layer', lp,
                '--backend', binary, '--system', system, '--network', network, '--output-dir', out/label]
        if mode is not None:
            observation = read(root/'fixtures/m2_single_layer/observation.json')
            observation.update(window_end_ns=1000000, record_provenance=mode)
            op = out/f'{label}.observation.json'; op.write_text(json.dumps(observation)+'\n')
            argv += ['--observation', op]
        return argv

    def run(label, config, layer, expected, mode=None):
        invoke(label, command(label, config, layer, mode))
        path = out/label
        front, result = read(path/'service_result.json'), read(path/'execution/result.json')
        manifest, evidence = read(path/'manifest.json'), read(path/'input_evidence.json')
        ranks = layer['rank_ids']; mapping = dict(zip(ranks, layer['rank_to_ub_node']))
        assert manifest['normal_exit'] and manifest['backend_exit_code'] == 0 and result['normal_exit']
        assert manifest['calibration_status'] == 'unvalidated_reference' and not manifest['full_model_execution_validated']
        assert manifest['latency_scope'] == 'selected_layer_iterations_only_not_full_model_ITL_or_TTFT'
        assert [r['weight_bytes'] for r in evidence['model']['ranks']] == expected['weights']
        assert [r['kv_capacity_blocks'] for r in evidence['model']['ranks']] == expected['blocks']
        assert result['rank_ids'] == ranks and result['rank_to_ub_node'] == layer['rank_to_ub_node']
        assert len(front['waves']) == expected['waves']
        assert len(result['transfers']) == expected['writes']
        assert sum(t['bytes'] for t in result['transfers']) == expected['bytes']
        assert result['network_initializations'] == 1
        assert result['network_drain']['idle']
        assert result['network_drain']['started_packets'] == result['network_drain']['arrived_packets']
        assert front['pending'] == front['waiting'] == front['running'] == front['inflight'] == [0]*len(ranks)
        assert result['active_wqes'] == [0]*len(ranks)
        assert all(r['finished'] and all(r[k] == 0 for k in
            ('cpu_slots', 'compute_slots', 'send_slots', 'pending_events', 'pending_tasks', 'stale_callbacks'))
            for r in result['rank_resources'])
        assert all(r['same_network_objects'] and r['pending_events'] == r['pending_tasks'] == 0
                   for r in result['boundary_resources'])
        assert all(not r['request_blocks'] and r['kv_used_bytes'] == 0 and r['used_bytes'] == r['non_kv_bytes']
                   for r in front['memory_final'])
        source = {r['request_id']: r for r in config['requests']}
        tokens = Counter()
        for wave in front['waves']:
            plan = read(path/f"graphs/{wave['graph_id']}/plan.json")
            check_sparse_graph(plan, result, path/'execution')
            assert wave['submitted_ns'] == plan['ready_ns']
            assert wave['finished_ns'] == max(e['time_ns'] for e in result['graph_completions']
                                               if e['graph_id'] == wave['graph_id'])
            assert plan['rank_to_ub_node'] == layer['rank_to_ub_node']
            for token in wave['tokens']:
                r = source[int(token['request_id'])]; step = tokens[r['request_id']]
                assert token['token_index'] == r['prompt_tokens']+r['generated_tokens']-1+step
                assert token['expert_ids'] == r['decode_routes'][step]
                tokens[r['request_id']] += 1
        for request in front['requests']:
            assert tokens[request['request_id']] == len(source[request['request_id']]['decode_routes'])
            assert request['status'] == 'FINISHED' and request['preemptions'] == 0
            assert request['num_tokens_reached'] == request['target_tokens']
        events = front['memory_events']
        admits = [e for e in events if e['event'] == 'admit']
        releases = [e for e in events if e['event'] == 'release']
        assert Counter(e['request_id'] for e in admits) == Counter(source.keys())
        assert Counter(e['request_id'] for e in releases) == Counter(source.keys())
        for event in admits:
            request = source[event['request_id']]
            assert event['num_computed_tokens'] == request['prompt_tokens']+request['generated_tokens']-1
            assert event['num_tokens_reached'] == event['num_computed_tokens']+1
            assert event['reserved_kv_tokens'] == event['num_computed_tokens']+len(request['decode_routes'])
        starts = {e['task']: e for e in result['wqe_starts']}
        commits = {e['task']: e for e in result['target_writes']}
        for transfer in result['transfers']:
            assert transfer['src_ub_node'] == mapping[transfer['src_rank']]
            assert transfer['dst_ub_node'] == mapping[transfer['dst_rank']]
            assert starts[transfer['ub_task_id']]['node'] == transfer['src_ub_node']
            commit = commits[transfer['ub_task_id']]
            assert (commit['src'], commit['dst']) == (transfer['src_ub_node'], transfer['dst_ub_node'])
            assert commit['time_ns'] == transfer['data_ready_ns'] < transfer['send_done_ns']
        if mode is True:
            for record in rows(path/'execution/truth/transfer_registry.jsonl'):
                assert record['src_ub_node'] == mapping[record['src_rank']]
                assert record['dst_ub_node'] == mapping[record['dst_rank']]
            for observed, truth in zip(rows(path/'execution/observed/port_counters.jsonl'),
                                       rows(path/'execution/truth/port_byte_truth.jsonl')):
                assert observed['counted_packet_bytes'] == sum(truth[k] for k in
                    ('business_payload_bytes', 'application_metadata_bytes', 'data_protocol_overhead_bytes',
                     'control_packet_bytes', 'unattributed_bytes'))
        cases[label] = dict(**expected, finished_ns=front['finished_ns'])
        return front, result

    gqa, layer = read(fixture/'gqa-service.json'), read(fixture/'gqa-layer.json')
    expected = dict(weights=[255872,280448,231296], blocks=[3,3,2], waves=6, writes=24, bytes=4608)
    normal = run('gqa-on', gqa, layer, expected, True)
    assert run('gqa-off', gqa, layer, expected, False) == normal
    assert run('gqa-absent', gqa, layer, expected) == normal
    assert not (out/'gqa-off/execution/truth').exists()
    for p in (out/'gqa-on/execution/observed').iterdir():
        assert p.read_bytes() == (out/'gqa-off/execution/observed'/p.name).read_bytes()
    front = normal[0]
    admits = {e['request_id']:e for e in front['memory_events'] if e['event'] == 'admit'}
    release = {e['request_id']:e for e in front['memory_events'] if e['event'] == 'release'}
    assert admits[103]['time_ns'] == release[101]['time_ns'] == front['waves'][3]['finished_ns'] > 1000
    assert release[101]['ranks'][0]['kv_used_bytes'] == 0
    assert front['arrivals'][-1]['active_graph_id'] == 'g0'
    assert all(t['src_rank'] != 12 for w in front['waves'] for t in w['tokens'])
    assert any(t['src_rank'] == 12 for t in normal[1]['transfers'])  # Expert-only endpoint.

    mha, mlayer = read(fixture/'mha-service.json'), read(fixture/'mha-layer.json')
    run('mha', mha, mlayer, dict(weights=[74496,49920], blocks=[2,2], waves=2, writes=4, bytes=256))
    # Exactly one block: output-1=8 fits, although final output length is 9.
    short = deepcopy(mha); short['memory']['ranks'][0]['device_bytes'] -= 512
    short['requests'][0]['decode_routes'].pop()
    run('mha-one-block', short, mlayer, dict(weights=[74496,49920], blocks=[1,2], waves=1, writes=2, bytes=128))
    empty = deepcopy(gqa); empty['requests'] = []
    run('empty', empty, layer, dict(weights=expected['weights'], blocks=[3,3,2], waves=0, writes=0, bytes=0))

    # A request whose initial context fits but whose final input needs two blocks
    # must fail before creating a run directory or starting the backend.
    impossible = deepcopy(mha); impossible['memory']['ranks'][0]['device_bytes'] -= 512
    process = invoke('reject-final-kv', command('reject-final-kv', impossible, mlayer), 1)
    assert 'final KV demand 2 blocks' in process.stderr and not (out/'reject-final-kv').exists()
    failures['final-kv'] = 'rejected before backend startup'

    # Replay the genuine first COMMIT, mutate the deployment/plan, and exercise
    # backend validation independently of Python's input checks.
    first_commit = rows(out/'gqa-on/protocol.jsonl')[1]
    invalid = {
        'duplicate-endpoint': lambda g: g['rank_to_ub_node'].__setitem__(1, 3),
        'switch-endpoint': lambda g: g['rank_to_ub_node'].__setitem__(1, 4),
        'missing-endpoint': lambda g: g['rank_to_ub_node'].__setitem__(1, 99),
        'wrong-endpoint': lambda g: g['rank_to_ub_node'].__setitem__(1, 1),
        'expert-outside': lambda g: g['expert_placement'].__setitem__(0, 99),
        'wrong-placement': lambda g: g['expert_placement'].__setitem__(0, 7),
        'duplicate-rank': lambda g: g['rank_ids'].__setitem__(1, 2),
        'rank-overflow': lambda g: g['rank_ids'].__setitem__(2, 2**31-1),
    }
    for name, mutate in invalid.items():
        shared = out/('bad-'+name); shared.mkdir()
        shutil.copytree(out/'gqa-on/bootstrap', shared/'bootstrap')
        shutil.copytree(out/'gqa-on/graphs/g0', shared/'graphs/g0')
        deployment = read(out/'gqa-on/deployment.json'); mutate(deployment['groups'][0])
        (shared/'deployment.json').write_text(json.dumps(deployment)+'\n')
        execution = shared/'execution'
        process = invoke('reject-'+name, [binary, gqa['run_id'], shared, system, network, '-', execution, 1000000],
                         1, json.dumps(first_commit)+'\n')
        assert not (execution/'result.json').exists()
        if (execution/'failure.json').exists():
            failure = read(execution/'failure.json')
            assert failure['now_ns'] == 0 and not failure['transfers']
        if (execution/'log/log.log').exists():
            assert 'graph_id=g0' not in (execution/'log/log.log').read_text()
        failures[name] = process.stderr.splitlines()[-1]
    sources = [p for folder in ('backend','python/moe_ub_cosim','fixtures/m6_service_model')
               for p in (root/folder).glob('*') if p.is_file()]
    (out/'summary.json').write_text(json.dumps(dict(passed=True, cases=cases, failures=failures,
        source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}), indent=2)+'\n')
    print('PASS: configured models, exact rank budgets, final-input reservation, lifecycle and real mapped UB writes')


if __name__ == '__main__':
    main()
