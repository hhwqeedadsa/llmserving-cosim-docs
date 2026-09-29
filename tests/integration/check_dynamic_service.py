"""Run actual ns-3 arrivals, independently busy EP groups, and protocol rejection."""
import argparse
from collections import Counter
from copy import deepcopy
import datetime
import json
from pathlib import Path
import selectors
import shutil
import subprocess
import sys

from check_delay import check_execution, read, sha
from check_variants import rows
from moe_ub_cosim.service import read_line


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, out = args.build_dir.resolve(), args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    fixture = root / 'fixtures/m3_dynamic_arrivals'
    binary = build / 'output/cosim/ns3.44-cosim-service'
    layer = root / 'fixtures/m2_single_layer/layer.json'
    system = root / 'fixtures/m1_compute/system.json'
    legacy = read(root / 'fixtures/m3_service/service.json')
    busy, two = read(fixture / 'busy.json'), read(fixture / 'two_groups.json')
    commands, results = [], {}

    def record(label, argv, result, start, **extra):
        (out / (label + '.stdout.log')).write_text(result.stdout)
        (out / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode, **extra))
        (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')

    def run(label, config, observed=None):
        input_path = out / (label + '.input.json')
        input_path.write_text(json.dumps(config, indent=2) + '\n')
        network = fixture / 'network' if len(config.get('groups', [])) == 2 else root / 'fixtures/m2_single_layer/network'
        argv = [sys.executable, '-m', 'moe_ub_cosim.service', '--input', str(input_path), '--layer', str(layer),
            '--backend', str(binary), '--system', str(system), '--network', str(network), '--output-dir', str(out / label)]
        if observed is not None:
            obs = read(fixture / 'observation.json'); obs['record_provenance'] = observed
            obs_path = out / (label + '.observation.json'); obs_path.write_text(json.dumps(obs) + '\n')
            argv.extend(['--observation', str(obs_path)])
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=90)
        record(label, argv, result, start)
        assert result.returncode == 0, (label, result.stderr)
        path = out / label
        front, report = read(path / 'service_result.json'), read(path / 'execution/result.json')
        transcript = rows(path / 'protocol.jsonl')
        assert read(path / 'manifest.json')['normal_exit'] and report['normal_exit'] and front['service_finished']
        count = 8 if 'groups' in config and len(config['groups']) == 2 else 4
        assert report['network_initializations'] == 1 and report['loaded_ranks_before_activation'] == count
        assert report['bootstrap'] == dict(business_nodes=0, finished_ranks=count, time_ns=0)
        assert front['waiting'] == front['running'] == front['inflight'] == front['pending'] == [0] * count
        assert front['scheduler_calls'] == len(front['waves']) * 4
        assert report['decisions'] == front['decisions'] == len(transcript) // 2
        assert report['network_drain']['idle'] and report['network_drain']['started_packets'] == report['network_drain']['arrived_packets']
        assert front['finished_ns'] == report['service_finished_ns'] <= report['simulation_end_ns'] < 1000000
        assert report['csv_completed_tasks'] == 0 and report['active_wqes'] == [0] * count
        assert all(r['finished'] and not r['remaining_nodes'] and r['pending_events'] == r['pending_tasks'] ==
                   r['send_slots'] == r['compute_slots'] == r['stale_callbacks'] == 0 for r in report['rank_resources'])
        assert all(b['same_network_objects'] and b['pending_events'] == b['pending_tasks'] == 0 for b in report['boundary_resources'])
        expected = {r['request_id']: r for r in config['requests']}
        assert Counter(a['request_id'] for a in front['arrivals']) == Counter(expected.keys())
        assert all(a['arrival_ns'] == expected[a['request_id']]['arrival_ns'] for a in front['arrivals'])
        tokens, plans, operations, transfers, tasks, tags = [], {}, set(), set(), set(), set()
        for wave in front['waves']:
            graph = wave['graph_id']
            plan = read(path / f'graphs/{graph}/plan.json'); plans[graph] = plan
            assert wave['submitted_ns'] == plan['ready_ns']
            assert plan['rank_ids'] == wave['rank_ids'] and plan['input']['group_id'] == wave['group_id']
            local = dict(loaded_ranks_before_activation=4)
            for key in ('activation_order', 'graph_completions', 'node_execution', 'transfers'):
                local[key] = [{k:v for k,v in e.items() if k != 'graph_id'} for e in report[key] if e['graph_id'] == graph]
            check_execution(plan, local, path / 'execution', check_rank2_ahead=False, graph_id=graph)
            assert wave['finished_ns'] == max(c['time_ns'] for c in local['graph_completions'])
            assert [e for e in report['graph_loads'] if e['graph_id'] == graph] == [
                dict(graph_id=graph, rank_id=r, time_ns=wave['submitted_ns']) for r in wave['rank_ids']]
            for table, field, seen in ((plan['operations'], 'operation_id', operations),
                    (plan['transfers'], 'transfer_id', transfers), (plan['transfers'], 'ub_task_id', tasks),
                    (plan['transfers'], 'comm_tag', tags)):
                for item in table:
                    assert item[field] not in seen
                    seen.add(item[field])
            tokens.extend(wave['tokens'])
            for token in wave['tokens']:
                source = expected[int(token['request_id'])]
                assert source['arrival_ns'] <= wave['submitted_ns']
                offset = token['token_index'] - source['prompt_tokens'] - source['generated_tokens'] + 1
                assert token['expert_ids'] == source['decode_routes'][offset]
        for request in front['requests']:
            source = expected[request['request_id']]
            mine = [t for t in tokens if int(t['request_id']) == request['request_id']]
            initial = source['prompt_tokens'] + source['generated_tokens']
            assert [t['token_index'] for t in mine] == list(range(initial - 1, initial - 1 + len(source['decode_routes'])))
            assert request['status'] == 'FINISHED' and request['num_tokens_reached'] == request['target_tokens']
            assert request['num_tokens_reached'] == initial + len(source['decode_routes']) and request['preemptions'] == 0
            assert sum(request['intertoken_latencies_ns']) == request['latest_completion_ns'] - source['arrival_ns']
        assert len(transfers) == len(report['transfers']) == len(report['wqe_starts']) == len(report['wqe_completions'])
        active, previous_time, seen_completions = {}, 0, set()
        for index in range(0, len(transcript), 2):
            decide, commit = transcript[index:index+2]
            now = decide['now_ns']
            assert now == commit['now_ns'] >= previous_time
            assert decide['decision_id'] == commit['decision_id'] == index // 2
            order = []
            for event in decide['completed']:
                graph = event['graph_id']; plan = plans[graph]; group = plan['input']['group_id']
                assert active.pop(group) == graph and graph not in seen_completions
                assert event['finished_ns'] == now == next(w['finished_ns'] for w in front['waves'] if w['graph_id'] == graph)
                order.append(group); seen_completions.add(graph)
            assert order == sorted(order)
            occupied = {r for g in active.values() for r in plans[g]['rank_ids']}
            assert decide['available_rank_ids'] == [r for r in range(count) if r not in occupied]
            for submission in commit['submissions']:
                graph = submission['graph_id']; group = plans[graph]['input']['group_id']
                assert group not in active
                active[group] = graph
            future = sorted({r['arrival_ns'] for r in config['requests'] if r['arrival_ns'] > now})
            assert commit['next_wakeup_ns'] == (future[0] if future else None)
            assert commit['input_exhausted'] == (not future)
            assert commit['service_finished'] == (index == len(transcript) - 2)
            # Every decision follows a real arrival or a reported wave completion.
            assert now == 0 or decide['completed'] or now in {r['arrival_ns'] for r in config['requests']}
            previous_time = now
        assert not active and len(seen_completions) == len(front['waves'])
        results[label] = (front, report, transcript)
        return front, report, transcript

    f, r, messages = run('busy', busy)
    arrival = next(i for i in range(0, len(messages), 2) if messages[i]['now_ns'] == 5200)
    assert messages[arrival]['available_rank_ids'] == [] and messages[arrival]['completed'] == []
    assert messages[arrival+1]['submissions'] == []
    assert f['decision_state'][0]['request_state'] == f['decision_state'][1]['request_state']
    assert all(a['active_graph_id'] == 'g0' for a in f['arrivals'] if a['arrival_ns'] == 5200)
    assert f['waves'][3]['submitted_ns'] == f['waves'][2]['finished_ns'] > 5200
    assert any(t['send_start_ns'] < 5200 < t['send_done_ns'] for t in r['transfers'])
    f, r, messages = run('two', two, True)
    arrival = next(i for i in range(0, len(messages), 2) if messages[i]['now_ns'] == 5500)
    assert messages[arrival]['available_rank_ids'] == [4,5,6,7]
    assert messages[arrival+1]['submissions'][0]['rank_ids'] == [4,5,6,7]
    assert f['waves'][1]['submitted_ns'] == 5500 < f['waves'][0]['finished_ns']
    assert any(t['src_rank'] < 4 and t['send_start_ns'] < 5500 < t['send_done_ns'] for t in r['transfers'])
    for label, observed in (('off', False), ('absent', None), ('repeat', True)):
        got = run(label, two, observed)
        assert got == results['two'], label
        original = out / 'two/execution'
        execution = out / label / 'execution'
        assert {str(p.relative_to(execution / 'network/runlog')):sha(p) for p in (execution / 'network/runlog').rglob('*') if p.is_file()} == {
            str(p.relative_to(original / 'network/runlog')):sha(p) for p in (original / 'network/runlog').rglob('*') if p.is_file()}
        if observed is not None:
            assert {p.name:sha(p) for p in (execution / 'observed').iterdir()} == {p.name:sha(p) for p in (original / 'observed').iterdir()}
    # No local records collide across groups or waves; observed output has no truth IDs.
    endpoints = rows(out / 'two/execution/observed/endpoint_ops.jsonl')
    assert len(endpoints) == len({(e['rank_id'],e['local_record_id']) for e in endpoints}) == 8 * len(f['waves'])
    assert all('graph_id' not in e and 'request_id' not in e for e in endpoints)
    operations = rows(out / 'two/execution/truth/operation_registry.jsonl')
    assert {o['group_id'] for o in operations} == {'ep0', 'ep1'}
    for op in operations:
        ranks = [0,1,2,3] if op['group_id'] == 'ep0' else [4,5,6,7]
        assert op['participants'] == [e['rank_id'] for e in op['endpoint_records']] == ranks
    # Initial all-idle time jump; future arrivals survive several completed waves.
    idle = deepcopy(legacy)
    for request in idle['requests']: request['arrival_ns'] = 9000
    _, _, messages = run('idle-jump', idle)
    assert [m['now_ns'] for m in messages[:4:2]] == [0,9000] and messages[1]['submissions'] == []
    renewal = deepcopy(busy)
    for request in renewal['requests'][4:]: request['arrival_ns'] = 100000
    front, _, messages = run('renewal', renewal)
    assert front['waves'][3]['submitted_ns'] == 100000
    assert sum(m['type'] == 'DECIDE' and m['now_ns'] == 100000 for m in messages) == 1
    assert all(m['next_wakeup_ns'] == 100000 for m in messages if m['type'] == 'COMMIT' and m['now_ns'] < 100000)
    # Arrival exactly at a known baseline wave completion: no duplicate admission.
    same = deepcopy(busy)
    for request in same['requests'][4:]: request['arrival_ns'] = results['busy'][0]['waves'][0]['finished_ns']
    run('same-time', same)
    simultaneous = deepcopy(two)
    simultaneous['requests'] = [q for q in simultaneous['requests'] if q['request_id'] < 200 or q['request_id'] >= 300]
    for request in simultaneous['requests']: request['arrival_ns'] = 0
    run('simultaneous', simultaneous)

    # Replay valid prefix then inject errors while A is actually executing.
    saved = results['two'][2]
    def reject(label, index, mutate, shared=None):
        path = out / ('rejected-' + label)
        shared = shared or out / 'two'
        argv = [str(binary), two['run_id'], str(shared), str(system), str(fixture / 'network'), '-', str(path), '1000000']
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        received, sent = [], []
        with (out / ('rejected-' + label + '.stderr.log')).open('w') as error:
            child = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error, bufsize=0)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    buffer = bytearray()
                    for step in range(index + 1):
                        message = json.loads(read_line(child, selector, buffer, 30)); received.append(message)
                        assert message == saved[2*step]
                        response = deepcopy(saved[2*step+1])
                        if step == index: mutate(response)
                        sent.append(response)
                        child.stdin.write((json.dumps(response) + '\n').encode()); child.stdin.flush()
                    child.stdin.close()
                    code = child.wait(timeout=30)
                    assert code == 1 and not buffer and child.stdout.read() == b''
            finally:
                if child.poll() is None: child.kill()
                child.wait(); child.stdout.close()
                if not child.stdin.closed: child.stdin.close()
        commands.append(dict(label='rejected-' + label, argv=argv, cwd=str(root), started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=child.returncode))
        (out / ('rejected-' + label + '.protocol.json')).write_text(json.dumps(dict(received=received, sent=sent),indent=2)+'\n')
        (out / 'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        error = (out / ('rejected-' + label + '.stderr.log')).read_text()
        assert 'cosim-service:' in error and not (path / 'result.json').exists(), (label,error)
        raw = (path / 'log/log.log').read_text()
        assert 'graph_id=g1' not in raw, label  # No part of the invalid group's graph executes.
        return error

    assert 'busy EP group' in reject('busy-replacement', 1, lambda c:c.update(submissions=deepcopy(saved[1]['submissions'])))
    assert 'active groups' in reject('premature-finish', 1, lambda c:c.update(service_finished=True, input_exhausted=True, next_wakeup_ns=None))
    for label, value in [('past', 5199), ('equal',5200), ('boolean',True), ('float',5500.5), ('overflow',2**63)]:
        assert 'future absolute' in reject('wakeup-' + label, 1, lambda c,v=value:c.update(next_wakeup_ns=v))
    assert 'Unknown' in reject('partial-group', 2, lambda c:c['submissions'][0].update(rank_ids=[4,5,6]))
    assert 'reused' in reject('reused-graph', 2, lambda c:c['submissions'][0].update(graph_id='g0'))
    broken = out / 'broken-shared'; broken.mkdir()
    shutil.copyfile(out / 'two/deployment.json', broken / 'deployment.json')
    shutil.copytree(out / 'two/bootstrap', broken / 'bootstrap')
    for graph in ('g0','g1'): shutil.copytree(out / 'two/graphs' / graph, broken / 'graphs' / graph)
    (broken / 'graphs/g1/layer.7.et').write_bytes(b'\x80')
    assert 'layer.7.et' in reject('last-group-rank', 2, lambda c:None, broken)
    (out / 'summary.json').write_text(json.dumps(dict(passed=True, actual_runs=len(results),
        rejected=[c['label'] for c in commands if c['label'].startswith('rejected-')],
        cases={label:dict(decisions=f['decisions'], waves=len(f['waves']), finished_ns=f['finished_ns'],
            writes=len(r['transfers']), bytes=sum(t['bytes'] for t in r['transfers']),
            wave_times=[dict(group_id=w['group_id'], start_ns=w['submitted_ns'], end_ns=w['finished_ns']) for w in f['waves']])
            for label,(f,r,_) in results.items()},
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim')
            for p in sorted((root / folder).glob('*')) if p.is_file()}),indent=2)+'\n')
    print('PASS: busy arrivals queue, idle groups start on time, wakeups survive completions, independent groups and unchanged observations')


if __name__ == '__main__':
    main()
