"""Real LLMServingSim requests and repeated ASTRA graphs sharing one UB network."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from check_delay import check_execution, read, sha
from check_variants import rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    build, output = args.build_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = build / 'output/cosim/ns3.44-cosim-service'
    fixture = root / 'fixtures/m3_service/service.json'
    layer = root / 'fixtures/m2_single_layer/layer.json'
    system = root / 'fixtures/m1_compute/system.json'
    network = root / 'fixtures/m2_single_layer/network'
    config = read(fixture)
    commands = []

    def record(label, argv, result, start):
        (output / (label + '.stdout.log')).write_text(result.stdout)
        (output / (label + '.stderr.log')).write_text(result.stderr)
        commands.append(dict(label=label, argv=argv, cwd=str(root), started_utc=start,
            finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), exit_code=result.returncode))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')

    def invoke(label, argv):
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=90, check=False)
        record(label, argv, result, start)
        return result

    reports, fronts, evidence, observations, audits = [], [], [], [], []
    slow = output / 'slow-network'; shutil.copytree(network, slow)
    topology = list(csv.DictReader((slow / 'topology.csv').open()))
    topology[1]['delay'] = '2020ns'
    with (slow / 'topology.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=topology[0].keys()); writer.writeheader(); writer.writerows(topology)
    observation = read(root / 'fixtures/m2_single_layer/observation.json')
    # Delayed case may run past the original single-layer observation interval.
    observation['window_end_ns'] = 1000000
    for mode in ('on', 'off', 'absent', 'repeat', 'slow'):
        obs = output / (mode + '.json')
        obs.write_text(json.dumps(dict(observation, record_provenance=mode != 'off')) + '\n')
        run = output / mode
        argv = [sys.executable, '-m', 'moe_ub_cosim.service', '--input', str(fixture), '--layer', str(layer),
            '--backend', str(binary), '--system', str(system), '--network', str(slow if mode == 'slow' else network),
            '--output-dir', str(run)]
        if mode != 'absent': argv += ['--observation', str(obs)]
        result = invoke(mode, argv)
        assert result.returncode == 0, (mode, result.stdout, result.stderr)
        front, report = read(run / 'service_result.json'), read(run / 'execution/result.json')
        assert read(run / 'manifest.json')['normal_exit'] and report['normal_exit']
        assert report['network_initializations'] == 1 and report['loaded_ranks_before_activation'] == 4
        assert report['bootstrap'] == dict(finished_ranks=4, time_ns=0, business_nodes=0)
        assert report['decisions'] == front['decisions'] == 4 and len(front['waves']) == 3
        assert front['scheduler_calls'] == 12 and front['service_finished']
        assert front['waiting'] == front['running'] == front['inflight'] == [0] * 4 and front['done'] == [1] * 4
        assert len(report['transfers']) == len(report['wqe_starts']) == len(report['wqe_completions']) == 42
        assert sum(t['bytes'] for t in report['transfers']) == 344064
        assert report['csv_completed_tasks'] == 0 and report['active_wqes'] == [0] * 4
        assert len(report['boundary_resources']) == 16 and all(b['same_network_objects'] and
            b['pending_events'] == b['pending_tasks'] == 0 for b in report['boundary_resources'])
        transcript = rows(run / 'protocol.jsonl')
        assert len(transcript) == 8
        previous = 0
        for step in range(4):
            decide, commit = transcript[2*step:2*step+2]
            assert decide['schema_version'] == commit['schema_version'] == 'cosim/1'
            assert decide['type'] == 'DECIDE' and commit['type'] == 'COMMIT'
            assert decide['decision_id'] == commit['decision_id'] == step
            assert decide['now_ns'] == commit['now_ns'] == previous
            assert decide['completed'] == ([] if step == 0 else [dict(kind='WAVE_DONE', wave_id=f'D.w{step-1}',
                graph_id=f'g{step-1}', finished_ns=previous)])
            assert commit['input_exhausted'] and commit['next_wakeup_ns'] is None
            assert commit['service_finished'] == (step == 3)
            if step == 3:
                assert commit['submissions'] == []
                break
            wave, graph = front['waves'][step], f'g{step}'
            plan = read(run / f'graphs/{graph}/plan.json')
            assert wave['submitted_ns'] == plan['ready_ns'] == previous
            assert len(plan['transfers']) == 14
            local = dict(loaded_ranks_before_activation=4)
            for key in ('activation_order', 'graph_completions', 'node_execution', 'transfers'):
                local[key] = [{k:v for k,v in e.items() if k != 'graph_id'} for e in report[key] if e['graph_id'] == graph]
            check_execution(plan, local, run / 'execution', check_rank2_ahead=mode != 'slow', graph_id=graph)
            assert [e for e in report['graph_loads'] if e['graph_id'] == graph] == [
                dict(graph_id=graph, rank_id=rank, time_ns=previous) for rank in range(4)]
            finished = max(e['time_ns'] for e in local['graph_completions'])
            assert finished > min(e['time_ns'] for e in local['graph_completions'])
            assert wave['finished_ns'] == finished
            for rank, source in enumerate(config['requests']):
                initial = source['prompt_tokens'] + source['generated_tokens']
                before, scheduled, after = wave['before'][rank], wave['scheduled'][rank], wave['after'][rank]
                assert before['num_tokens_reached'] == scheduled['num_tokens_reached'] == initial + step
                assert before['num_computed_tokens'] + 1 == scheduled['num_computed_tokens'] == after['num_computed_tokens']
                assert after['num_tokens_reached'] == initial + step + 1
                assert after['status'] == ('FINISHED' if step == 2 else 'RUNNING')
                assert after['latest_completion_ns'] == finished
                assert plan['input']['tokens'][rank]['token_index'] == initial - 1 + step
                assert plan['input']['tokens'][rank]['expert_ids'] == source['decode_routes'][step]
            previous = finished
        assert front['finished_ns'] == report['service_finished_ns'] == previous
        assert report['simulation_end_ns'] >= previous and report['simulation_end_ns'] < 1000000
        for rank, final in enumerate(front['requests']):
            assert final['status'] == 'FINISHED' and final['num_tokens_reached'] == final['target_tokens']
            assert final['decode_step'] == rank + 4 and final['preemptions'] == 0
            assert sum(final['intertoken_latencies_ns']) == previous
        for key in ('transfer_id', 'ub_task_id'):
            assert len({t[key] for t in report['transfers']}) == 42
        assert all(r['finished'] and not r['remaining_nodes'] and r['pending_events'] == r['pending_tasks'] ==
                   r['send_slots'] == r['compute_slots'] == r['stale_callbacks'] == 0 for r in report['rank_resources'])
        raw = (run / 'execution/log/log.log').read_text()
        assert not re.search(r'(issue|callback),.*graph_id=_bootstrap', raw)
        assert len(re.findall(r'(?:issue|callback),sys->id=', raw)) == 288
        current_evidence = dict(astra=re.sub(r'^\[\d{4}-\d{2}-\d{2} [^]]+\] ', '', raw, flags=re.MULTILINE),
            native={str(p.relative_to(run / 'execution')): sha(p) for p in (run / 'execution/network/runlog').rglob('*') if p.is_file()})
        reports.append(report); fronts.append(front); evidence.append(current_evidence)
        if mode == 'absent':
            assert not (run / 'execution/observed').exists(); continue
        counter = rows(run / 'execution/observed/port_counters.jsonl')
        endpoint = rows(run / 'execution/observed/endpoint_ops.jsonl')
        audit = read(run / 'execution/observation-audit.json')
        assert len(endpoint) == len({(r['rank_id'],r['local_record_id']) for r in endpoint}) == 24
        native = (run / 'execution/network/runlog/PortTrace_node_4_port_2.tr').read_text()
        starts = [(int(Decimal(t)*1000), int(n)) for t,n in re.findall(
            r'\[([\d.]+)us\] Port Tx, port ID: 2 PacketSize: (\d+)',native)]
        for c in counter:
            sizes = [n for t,n in starts if c['local_window_start_ns'] <= t < c['local_window_end_ns']]
            assert c['counted_packets'] == len(sizes) and c['counted_packet_bytes'] == sum(sizes)
        hidden = ('graph_id','operation_id','transfer_id','comm_tag','request_id','token_index')
        assert all(all(k not in row for k in hidden) for row in endpoint + counter)
        assert sum(c['counted_packet_bytes'] for c in counter) == audit['native_tx_bytes_delta']
        if mode != 'slow':
            observations.append({p.name:sha(p) for p in (run / 'execution/observed').iterdir()}); audits.append(audit)
        if mode == 'off':
            assert not (run / 'execution/truth').exists(); continue
        truth = run / 'execution/truth'
        assert len(rows(truth / 'transfer_registry.jsonl')) == 42
        operations = rows(truth / 'operation_registry.jsonl')
        assert len(operations) == 6 and {o['graph_id'] for o in operations} == {'g0','g1','g2'}
        body = rows(truth / 'port_byte_truth.jsonl')
        assert sum(b['business_payload_bytes'] for b in body) == 73728
        for c,b in zip(counter,body):
            assert c['counted_packet_bytes'] == sum(b[k] for k in ('business_payload_bytes',
                'application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes'))
    assert all(r == reports[0] for r in reports[:4]) and all(f == fronts[0] for f in fronts[:4])
    assert all(e == evidence[0] for e in evidence[:4]) and all(o == observations[0] for o in observations)
    assert all(a == audits[0] for a in audits)
    assert fronts[-1]['finished_ns'] > fronts[0]['finished_ns']
    for a,b in zip(fronts[0]['waves'],fronts[-1]['waves']):
        assert a['tokens'] == b['tokens'] and a['finished_ns'] < b['finished_ns']
    # Replaying the same COMMIT after a wall-clock pause must not add simulated time.
    argv = [str(binary), config['run_id'], str(output / 'on'), str(system), str(network), '-', str(output / 'paused'), '1000000']
    start = datetime.datetime.now(datetime.timezone.utc).isoformat()
    saved = rows(output / 'on/protocol.jsonl')
    with (output / 'paused.stderr.log').open('w') as error:
        child = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error, text=True)
        received = []
        for step in range(4):
            message = json.loads(child.stdout.readline()); received.append(message)
            assert message == saved[2*step]
            time.sleep(0.03)
            child.stdin.write(json.dumps(saved[2*step+1]) + '\n'); child.stdin.flush()
        child.stdin.close()
        assert child.wait(timeout=30) == 0 and child.stdout.read() == ''
        child.stdout.close()
    commands.append(dict(label='paused', argv=argv, cwd=str(root), started_utc=start, exit_code=child.returncode,
        finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), injected_wall_pause_ms=30))
    (output / 'paused.protocol.json').write_text(json.dumps(received,indent=2)+'\n')
    assert read(output / 'paused/result.json') == reports[0]
    # Short final COMP nodes and a real port interframe gap leave packets/ports
    # active after the last wave. Shutdown must wait for that actual tail.
    tail_layer = read(layer)
    tail_layer['compute_profile_ns'].update(combine_unpack_merge=1, next_layer=1)
    (output / 'tail-layer.json').write_text(json.dumps(tail_layer) + '\n')
    tail_network = output / 'tail-network'; shutil.copytree(network, tail_network)
    attributes = tail_network / 'network_attribute.txt'
    attributes.write_text(attributes.read_text().replace('UbInterframeGap "+0ns"', 'UbInterframeGap "+500ns"'))
    result = invoke('tail', [sys.executable, '-m', 'moe_ub_cosim.service', '--input', str(fixture),
        '--layer', str(output / 'tail-layer.json'), '--backend', str(binary), '--system', str(system),
        '--network', str(tail_network), '--output-dir', str(output / 'tail')])
    assert result.returncode == 0, result.stderr
    tail = read(output / 'tail/execution/result.json')
    assert tail['normal_exit'] and tail['simulation_end_ns'] > tail['service_finished_ns']
    assert tail['network_drain']['idle'] and tail['network_drain']['started_packets'] == tail['network_drain']['arrived_packets']
    assert read(output / 'tail/service_result.json')['finished_ns'] == tail['service_finished_ns']
    # Protocol faults must fail at the initial decision, without executing a partial graph.
    base = deepcopy(saved[1])
    bad = {'eof': None, 'json': '{broken', 'decision': dict(base,decision_id=99),
           'time': dict(base,now_ns=1), 'run': dict(base,run_id='other'),
           'future': dict(base,next_wakeup_ns=10), 'no-work': dict(base,submissions=[])}
    wrong = deepcopy(base); wrong['submissions'][0]['semantic_manifest'] = '../outside/plan.json'; bad['path'] = wrong
    bad_graph = output / 'bad-graph'; shutil.copytree(output / 'on/bootstrap', bad_graph / 'bootstrap')
    shutil.copytree(output / 'on/graphs/g0', bad_graph / 'graphs/g0')
    (bad_graph / 'graphs/g0/layer.3.et').write_bytes(b'\x80')
    bad['last-rank'] = base
    for label, message in bad.items():
        run = output / ('rejected-' + label)
        argv = [str(binary), config['run_id'], str(bad_graph if label == 'last-rank' else output / 'on'),
            str(system), str(network), '-', str(run), '1000000']
        start = datetime.datetime.now(datetime.timezone.utc).isoformat()
        text = '' if message is None else ((message if isinstance(message,str) else json.dumps(message)) + '\n')
        result = subprocess.run(argv,cwd=root,input=text,capture_output=True,text=True,timeout=30)
        record('rejected-' + label,argv,result,start)
        assert result.returncode == 1 and 'cosim-service:' in result.stderr, (label,result)
        assert len(result.stdout.splitlines()) == 1 and json.loads(result.stdout)['type'] == 'DECIDE'
        assert 'graph_id=g0' not in (run / 'log/log.log').read_text(), label
        assert not (run / 'result.json').exists()
    # A failed child must be visible in the front-end manifest; no silent fallback.
    result = invoke('child-failure',[sys.executable,'-m','moe_ub_cosim.service','--input',str(fixture),
        '--layer',str(layer),'--backend','/bin/false','--system',str(system),'--network',str(network),
        '--output-dir',str(output / 'child-failure')])
    failure = read(output / 'child-failure/manifest.json')
    assert result.returncode != 0 and not failure['normal_exit'] and failure['backend_exit_code'] == 1
    assert 'EOF' in failure['error']
    members = invoke('archive',['ar','t',str(build / 'cosim-m0/lib/libAstraSim.a')]).stdout.splitlines()
    recipe = invoke('commands',['ninja','-C',str(build),'-t','commands','cosim-service'])
    link = (build / 'cosim-m3/service-link.map').read_text()
    assert len(members) == 52 and not any(v in link + recipe.stdout for v in ('network_frontend/','extern/network_backend/'))
    assert Counter(re.findall(r'libAstraSim\.a\(([^)]+)\)\s+\(--whole-archive\)',link)) == Counter(members)
    modules = ('applications','bridge','config-store','core','internet','network','point-to-point','stats','traffic-control','unified-bus')
    libraries = {build / 'output/lib' / f'libns3.44-{n}.so' for n in modules}
    assert {Path(p).resolve() for p in reports[0]['loaded_libraries'] if Path(p).name.startswith('libns3')} == libraries
    (output / 'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
    (output / 'summary.json').write_text(json.dumps(dict(passed=True, actual_runs=7, rejected=list(bad),
        tail_service_finished_ns=tail['service_finished_ns'], tail_network_drained_ns=tail['simulation_end_ns'],
        finished_ns=fronts[0]['finished_ns'], delayed_finished_ns=fronts[-1]['finished_ns'],
        wave_finished_ns=[w['finished_ns'] for w in fronts[0]['waves']],
        delayed_wave_finished_ns=[w['finished_ns'] for w in fronts[-1]['waves']],
        writes=42, business_bytes=344064, native_files=len(evidence[0]['native']), port_audit=audits[0],
        astra_core_objects=len(members), ub_ns3_libraries=len(libraries),
        source_hashes={str(p.relative_to(root)):sha(p) for folder in ('backend','python/moe_ub_cosim')
            for p in sorted((root / folder).glob('*')) if p.is_file()}),indent=2)+'\n')
    print('PASS: real scheduler, three UB waves, independent request progress, delayed feedback, protocol faults and passive observation')


if __name__ == '__main__':
    main()
