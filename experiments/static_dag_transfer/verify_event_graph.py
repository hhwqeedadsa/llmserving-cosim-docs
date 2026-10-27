"""Independent evidence counts and falsification probes; zero networks."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def gather(stage):
    paths = dict(graph='event-graph.json', ledger='execution.json', program='program.json',
                 base_model='reference/baseline/modern-execution.json', plan='reference/baseline/modern-plan.json',
                 transfer_model='reference/bandwidth/modern-execution.json')
    for name in ('baseline', 'bandwidth'):
        paths[name] = name+'/result.json'
        paths[name+'_reference'] = 'reference/'+name+'/result.json'
        paths[name+'_events'] = name+'/event-execution.json'
    return {k:json.loads((stage/v).read_bytes()) for k,v in paths.items()}


def audit(docs):
    graph, ledger = docs['graph'], docs['ledger']
    assert ledger['closed'] and ledger['network_executions'] == 2
    assert [r['name'] for r in ledger['records']] == ['baseline', 'bandwidth']
    assert all(r['state'] == 'completed' for r in ledger['records'])
    base = docs['base_model']
    expected = {e['event']:e for e in base['events']}
    plans = {e['id']:e for e in docs['plan']['events']}
    assert len(graph['events']) == len(expected) == 576
    assert graph['metadata']['reference_timestamps_embedded'] is False
    for e in graph['events']:
        assert not any('time_ns' in key for key in e)
        assert set(e['deps']) >= set(plans[e['id']]['deps'])
        old = expected[e['id']]
        callback = [old['native_operation'], old['native_event']] if 'native_operation' in old else None
        assert e['callback'] == callback
    program = docs['program']
    assert program['operations'] == base['native_operations']
    assert len(program['operations']) == 164 and len(program['connections']) == 29
    assert sum(n['kind']=='local' for n in program['operations']) == 54
    task_key = lambda t: json.dumps(t, sort_keys=True)
    stable = {task_key(t):int(i) for i,t in base['native_tasks'].items()}
    assert len(stable) == 164
    mapping = {int(i):stable[task_key(t)] for i,t in docs['transfer_model']['native_tasks'].items()}
    assert len(mapping) == len(set(mapping.values())) == 164
    independent_packets = independent_callbacks = 0
    for name in ('baseline', 'bandwidth'):
        old, new = docs[name+'_reference'], docs[name]
        assert new['passed'] and new['session']['finished'] and docs[name+'_events']['all_events_fired']
        assert new['session']['decisions'] <= 256
        assert new['packet_capture']['omitted_events'] == 0 and new['packet_capture']['complete']
        assert new['packet_capture'] == old['packet_capture']
        for key in ('source_packets', 'source_payload_packets'):
            assert new['transactions']['transport'][key] == old['transactions']['transport'][key]
        assert new['transactions']['windows'] == old['transactions']['windows']
        assert new['finish_ns'] == old['finish_ns']
        transform = mapping if name == 'bandwidth' else {i:i for i in mapping}
        callbacks = [dict(row, operation=transform[row['operation']]) for row in old['session']['events']]
        assert callbacks == new['session']['events']
        model = base if name == 'baseline' else docs['transfer_model']
        rows = []
        for row in model['events']:
            data = {k:row[k] for k in ('event','time_ns','native_operation','native_event','native_time_ns') if k in row}
            if 'native_operation' in data:
                data['native_operation'] = transform[data['native_operation']]
            rows.append(data)
        assert rows == docs[name+'_events']['events']
        independent_packets += len(new['transactions']['transport']['source_packets'])
        independent_callbacks += len(callbacks)
    return dict(passed=True, independently_counted_source_packets=independent_packets,
                independently_counted_callbacks=independent_callbacks, application_events_per_network=576,
                native_work_per_network=164, task_ids_renamed_in_transfer=sum(i!=j for i,j in mapping.items()),
                new_networks_during_verification=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    docs = gather(args.stage)
    result = audit(docs)
    graph_bytes = (args.stage/'event-graph.json').read_bytes()
    assert hashlib.sha256(graph_bytes).hexdigest() == docs['ledger']['event_graph_sha256']
    assert not (args.stage/'propagation').exists()
    def run_probe(name, change):
        altered = deepcopy(docs)
        change(altered)
        try:
            audit(altered)
        except AssertionError:
            rejected.append(name)
            return
        raise AssertionError('tampering accepted: '+name)
    rejected = []
    def change_start(d):
        e = next(e for e in d['graph']['events'] if e['callback'] == [28,'local_started'])
        e['callback'] = [28,'completed']
    run_probe('start_gate_replaced_with_completion', change_start)
    run_probe('false_success', lambda d:d['bandwidth'].__setitem__('passed',False))
    run_probe('interaction_limit_changed', lambda d:d['baseline']['session'].__setitem__('decisions',257))
    run_probe('extra_network', lambda d:d['ledger'].__setitem__('network_executions',3))
    run_probe('source_packet_time_changed', lambda d:d['bandwidth']['transactions']['transport']['source_packets'][0].__setitem__(0,1))
    def change_payload(d):
        p = d['bandwidth']['transactions']['transport']['source_payload_packets'][0]
        # Digest/content evidence must agree, not just the source packet count.
        field = next(k for k,v in p.items() if isinstance(v,str))
        p[field] += 'tampered'
    run_probe('source_payload_changed', change_payload)
    result['tampering_rejected'] = rejected
    result['strict_failed_evaluation_preserved'] = not json.loads((args.stage/'bandwidth-equivalence.json').read_bytes())['equivalent']
    result['graph_hash_verified'] = True
    (args.stage/'independent-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
