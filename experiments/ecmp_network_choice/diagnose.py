"""Explain the retained strict failure without changing the gate or any network."""
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
from pathlib import Path

from experiments.static_dag_transfer.diagnose_event_graph import semantic_identity,replace_operation_keys
from .data import load, save, sha


def execute(stage,source_root,output):
    root=stage/'truth/batch4'
    comparison=load(root/'quad/comparison.json')
    assert not comparison['equivalent']
    assert {k for k,v in comparison['checks'].items() if not v} == {'callbacks'}
    detailed=load(root/'quad/modern/modern-execution.json')
    baseline=load(root/'single/modern/modern-execution.json')
    lookup={semantic_identity(t):int(i) for i,t in baseline['native_tasks'].items()}
    mapping={int(i):lookup[semantic_identity(t)] for i,t in detailed['native_tasks'].items()}
    modern=replace_operation_keys(load(root/'quad/modern/result.json')['session']['events'],mapping)
    generic=load(root/'quad/generic/result.json')['session']['events']
    assert len(modern)==len(generic)
    def group(rows,key):
        grouped=defaultdict(list)
        for row in rows:grouped[row[key]].append(row)
        return dict(grouped)
    a,b=group(modern,'time_ns'),group(generic,'time_ns')
    assert set(a)==set(b)
    assert group(modern,'operation')==group(generic,'operation'),'per-operation history differs'
    changed=[]
    for time in a:
        assert Counter((r['operation'],r['event']) for r in a[time])==Counter((r['operation'],r['event']) for r in b[time])
        if a[time]!=b[time]:changed.append(dict(time_ns=time,modern=a[time],generic=b[time]))
    envelopes=[]
    import json
    for mode in ('modern','generic'):
        records=[json.loads(line)['message'] for line in (root/f'quad/{mode}/control.jsonl').read_text().splitlines()
            if json.loads(line)['direction']=='native']
        if mode=='modern':records=replace_operation_keys(records,mapping)
        records=deepcopy(records)
        for message in records:
            for batch in message['batches']:
                batch['events'].sort(key=lambda r:(r['time_ns'],r['operation'],r['event']))
            message['events'].sort(key=lambda r:(r['time_ns'],r['operation'],r['event']))
        envelopes.append(records)
    assert envelopes[0]==envelopes[1],'batch boundaries or another envelope field differs'
    source={}
    for name,needles in {
        'python/moe_ub_cosim/modern_native.py':('for row in decision[\'events\']:', 'self.callbacks[key]=row[\'time_ns\']','changed=True'),
        'experiments/static_dag_transfer/event_graph.py':("for callback in row['events']:","self.seen[key] = callback['time_ns']",'output = []'),
    }.items():
        path=source_root/name
        lines=path.read_text().splitlines()
        matches={needle:[i for i,line in enumerate(lines,1) if needle in line] for needle in needles}
        assert all(matches.values())
        source[name]=dict(sha256=sha(path),anchor_lines=matches)
    deltas={}
    prediction=load(stage/'predictions/batch4.json')
    times={name:load(root/name/'modern-check.json')['metrics']['application_ns'] for name in ('single','dual','quad')}
    for name in ('dual','quad'):
        p=prediction['predictions']
        delta=p[name]['methods']['hashed_link_phase']['estimated_application_ns']-p['single']['methods']['hashed_link_phase']['estimated_application_ns']
        deltas[name]=dict(actual_delta_ns=times[name]-times['single'],ordinary_predicted_delta_ns=delta,
            increment_residual_ns=(times[name]-times['single'])-delta,
            residual_is_not_independently_recoverable_loss=True)
    save(output,dict(passed=True,strict_failure_preserved=True,remaining_networks_closed=True,
        callbacks_per_execution=len(modern),mismatched_list_positions=sum(x!=y for x,y in zip(modern,generic)),
        changed_timestamp_buckets=len(changed),changes=changed,
        same_callback_multiset_at_every_timestamp=True,identical_per_operation_callback_history=True,
        all_native_envelopes_equal_after_within_batch_callback_ordering=True,native_envelopes_per_execution=len(envelopes[0]),
        controller_sources_cache_whole_batch_before_advancing=source,
        packets_queues_transactions_memory_and_application_already_exactly_equal=True,
        no_claim_of_original_strict_pass=True,new_networks=0,configuration_increment_comparison=deltas))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    execute(a.stage.resolve(),a.source_root.resolve(),a.output.resolve())
