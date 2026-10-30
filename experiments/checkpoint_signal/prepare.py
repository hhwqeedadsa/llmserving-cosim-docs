"""Calibrate normal checkpoints and export strictly past observation views."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}

def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')

def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def completed(checkpoint, observed, result):
    if checkpoint['kind'] == 'graph_retired':
        matches = [e for e in observed if e['kind'] == 'graph_retired' and e['graph_id'] == checkpoint['graph']]
        assert len(matches) == 1
        return matches[0]['time_ns']
    times = {(e['rank'], e['node']): e['time_ns'] for e in observed
             if e['kind'] == 'node_complete' and e['graph_id'] == checkpoint['graph']}
    values = [times[m['rank'], m['node']] for m in checkpoint['members']]
    # These are global zero-coordination GATE callbacks, not independent
    # local flush times. Preserve this model boundary explicitly.
    assert len(set(values)) == 1
    gate = next(g for g in result['service']['phase_completion']['gates']
                if g['graph_id'] == checkpoint['graph'] and g['id'] == 'L0.dispatch')
    assert gate['released_ns'] == values[0] == max(m['arrived_ns'] for m in gate['members'])
    return values[0]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-study', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    previous = load(args.previous_study/'frozen.json')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out/'observed').mkdir()
    (out/'truth').mkdir()
    records = {}
    for condition in ('normal', 'local'):
        path = Path(previous['cases']['control-'+condition]['output'])
        before = files(path)
        subprocess.run([sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-check', '--run', str(path)], cwd=ROOT, check=True)
        assert files(path) == before
        config, result = load(path/'input.json'), load(path/'result.json')
        assert result['gpu_count'] == 128 and result['drained'] and result['passed']
        assert config['workload']['decode_steps'] == 2
        assert config['parallel']['groups'] == [list(range(i, 128, 4)) for i in range(4)]
        observed = events(path/'execution_events.jsonl')
        native = [json.loads(line[len('MOE_RDMA_EVENT '):]) for line in (path/'native.stdout.log').read_text().splitlines()
                  if line.startswith('MOE_RDMA_EVENT ') and json.loads(line[len('MOE_RDMA_EVENT '):])['type'] == 'OBSERVE']
        assert native == observed
        assert [e['sequence'] for e in observed] == list(range(len(observed)))
        assert all(a['time_ns'] <= b['time_ns'] for a, b in zip(observed, observed[1:]))
        wave = load(path/'graphs/g0.w0/wave.json')
        records[condition] = dict(path=path, config=config, result=result, wave=wave, events=observed, sha256=before)
    normal = records['normal']
    assert normal['wave'] == records['local']['wave']
    assert normal['config'] == records['local']['config']
    assert [q['id'] for q in normal['wave']['phase_completion']['gates']] == ['L0.dispatch', 'L0.combine', 'L1.dispatch', 'L1.combine']
    members = deepcopy(normal['wave']['phase_completion']['gates'][0]['members'])
    assert len(members) == 32 and members == sorted(members, key=lambda m: m['rank'])
    definitions = dict(first_dispatch=dict(kind='phase_complete', graph='g0.w0', members=members, representative=members[0]),
                       first_decode=dict(kind='graph_retired', graph='g0.w0'),
                       complete_segment=dict(kind='graph_retired', graph='g0.w1'))
    truth, plans = {}, {}
    for name, checkpoint in definitions.items():
        time = completed(checkpoint, normal['events'], normal['result'])
        deadline = (time//1000+1)*1000
        plan = dict(schema_version='checkpoint-plan/1', name=name, checkpoint=checkpoint,
                    final_graph='g0.w1', simulated_endpoints=128, deadline_ns=deadline,
                    calibration=dict(kind='single_normal_sample_strict_next_1000ns/1', normal_time_ns=time))
        save(out/(name+'.json'), plan)
        plans[name] = str(out/(name+'.json'))
        truth[name] = dict(normal_time_ns=time, deadline_ns=deadline, conditions={})
        for condition, record in records.items():
            full = [event for event in record['events'] if event['time_ns'] < deadline]
            minimal = []
            for event in full:
                if event['kind'] == 'graph_retired' and event['graph_id'] in (checkpoint['graph'], plan['final_graph']):
                    minimal.append(event)
                elif (checkpoint['kind'] == 'phase_complete' and event['kind'] == 'node_complete' and
                      event['graph_id'] == checkpoint['graph'] and
                      (event['rank'], event['node']) == (members[0]['rank'], members[0]['node'])):
                    minimal.append(event)
            for suffix, view in (('full', full), ('minimal', minimal)):
                with (out/'observed'/f'{name}-{condition}-{suffix}.jsonl').open('x') as stream:
                    stream.write(''.join(json.dumps(e, separators=(',', ':'))+'\n' for e in view))
            truth[name]['conditions'][condition] = dict(actual_complete_ns=completed(checkpoint, record['events'], record['result']),
                past_events=len(full), minimal_past_events=len(minimal),
                whole_result_ns=load(record['path']/'whole_call_report.json')['calls']['target']['result_ready_ns'])
    save(out/'truth/calibration-and-evaluation.json', truth)
    source_paths = ['backend/rdma/execution_events.hh', 'backend/rdma/service.cc',
                    'python/moe_ub_cosim/scaleout_service.py', 'python/moe_ub_cosim/phase_completion.py',
                    'python/moe_ub_cosim/sequential_whole.py', 'python/moe_ub_cosim/service_wakeup.py']
    frozen = dict(passed=True, new_network_executions=0,
        platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        records={condition: dict(output=str(r['path']), sha256=r['sha256']) for condition, r in records.items()},
        initial_graph_sha256=sha(normal['path']/'graphs/g0.w0/wave.json'),
        source_sha256={name: sha(ROOT/name) for name in source_paths},
        source_lineage=previous['source_lineage'], simulated_endpoints=128, ep_sizes=[32]*4,
        plans=plans, method_sha256=sha(Path(__file__).with_name('protocol.md')),
        preparer_sha256=sha(Path(__file__)), classifier_sha256=sha(Path(__file__).with_name('classify.py')),
        prepared_sha256=files(out), limitation='zero-coordination globally released GATE; no actual stage-triggered policy or new network effect')
    save(out/'frozen.json', frozen)
    print(json.dumps(dict(passed=True, checkpoints=truth, new_network_executions=0)))

if __name__ == '__main__':
    main()
