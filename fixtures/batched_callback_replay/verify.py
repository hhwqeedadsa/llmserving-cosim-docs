"""Independent transcript/boundary/state oracle; imports no controller/frontier."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


CASES=('default-healthy','timed-healthy','wake-fault','four-routes')


def load(path):return json.loads(path.read_text())


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def verify(report,source):
    assert report['schema_version']=='moe-batched-callback-offline/1' and report['network_executed'] is False
    assert set(report['cases'])==set(CASES);summary={}
    for case in CASES:
        candidate=report['cases'][case];directory=source/case
        for name,expected in candidate['source_sha256'].items():assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==expected
        assert set(candidate['source_sha256'])=={'input.json','topology.json','protocol.json','control.jsonl','modern-execution.json'}
        original=load(directory/'modern-execution.json')
        assert candidate['offline_only'] and candidate['network_executed'] is False and candidate['divergence'] is None
        assert candidate['original_native_limit_unchanged']==256 and candidate['physical_interface_implemented'] is False
        assert candidate['same_ns_native_scheduler_order_verified'] is False
        assert candidate['representative_four_rank_call_complete'] is False
        assert candidate['baseline_full_snapshot_equal'] and candidate['reconstructed_full_snapshot_equal']
        reconstructed=candidate['reconstructed_snapshot_after_explicit_tail_unfold']
        assert reconstructed==original,'full reconstructed events/slots/bindings/memory/task state changed'
        rows=[json.loads(line) for line in (directory/'control.jsonl').read_text().splitlines()]
        assert len(rows)%2==0
        steps=candidate['logical_steps'];flushes=candidate['physical_flushes']
        assert len(steps)==len(rows)//2==candidate['original_logical_steps']==original['decisions']
        assert [step['original_decision'] for step in steps]==list(range(len(steps)))
        assert candidate['physical_flush_count']==len(flushes)
        deliveries={row['original_decision']:row for row in flushes}
        assert len(deliveries)==len(flushes) and list(deliveries)==sorted(deliveries)
        registered={};seen=set();groups=[];pending=[];mapping={};previous_ns=-1;previous_drained=False
        maxima=dict(batches=0,bytes=0,groups=0,terms=0);modern_events=[]
        source_input=load(directory/'input.json')
        local_rank_nodes={item['node'] for item in source_input['deployment']}
        for index,step in enumerate(steps):
            incoming,outgoing=rows[2*index:2*index+2]
            assert (incoming['direction'],outgoing['direction'])==('native','frontend')
            event,command=incoming['message'],outgoing['message']
            assert event['decision_id']==command['decision_id']==index
            assert previous_ns<=event['now_ns']==command['now_ns']==step['now_ns'];previous_ns=event['now_ns']
            assert step['callback_count']==len(event['events']) and step['callback_sha256']==digest(event['events'])
            assert step['native_submissions']==[node['id'] for node in command['submissions']]
            assert step['submission_sha256']==digest(command['submissions']) and step['finish']==command['finish']
            for row in event['events']:
                assert row['time_ns']<=event['now_ns']
                if 'alarm' in row:continue
                key=row['operation'],row['event'];assert key not in seen and key[0] in registered
                seen.add(key)
            pending.append((index,event))
            maxima['batches']=max(maxima['batches'],len(pending))
            payload=[dict(now_ns=e['now_ns'],events=e['events'],drained=e['drained']) for _,e in pending]
            maxima['bytes']=max(maxima['bytes'],len(json.dumps(payload,separators=(',',':')).encode()))
            triggered=any(all((term['operation'],term['event']) in seen for term in group) for group in groups)
            reason='first' if index==0 else 'alarm' if any('alarm' in row for row in event['events']) else (
                'new_drain' if event['drained'] and not previous_drained else 'submission_frontier' if triggered else None)
            previous_drained=event['drained']
            assert (index in deliveries)==(reason is not None),'physical wake differs from actual conditions'
            for fired in step['new_modern_events']:assert fired['time_ns']==step['now_ns']
            modern_events.extend(step['new_modern_events'])
            if reason is None:
                assert not command['submissions'] and not command['finish'],'an intermediate batch required immediate action'
                continue
            current=deliveries[index]
            assert current['reason']==reason and current['now_ns']==event['now_ns']
            assert current['original_batch_ids']==[i for i,_ in pending],'batch boundary/order lost'
            physical=flushes.index(current)
            for i,_ in pending:mapping[i]=physical
            pending=[]
            for node in command['submissions']:
                assert node['id'] not in registered
                if node['kind']=='local':assert node['rank'] in local_rank_nodes
                registered[node['id']]=node
            groups=current['next_conditions'];assert isinstance(groups,list)
            group_keys=[]
            for group in groups:
                assert isinstance(group,list) and group,'empty conjunction cannot predict progress'
                keys=[]
                for term in group:
                    assert set(term)=={'operation','event'} and type(term['operation']) is int
                    identity=term['operation'];key=identity,term['event']
                    assert identity in registered and key not in seen,'condition references future/already observed work'
                    node=registered[identity]
                    allowed={'local_started','completed'} if node['kind']=='local' else {'submitted','applied','completed'}
                    assert term['event'] in allowed
                    keys.append(key)
                assert len(keys)==len(set(keys));group_keys.append(tuple(sorted(keys)))
            assert len(group_keys)==len(set(group_keys))
            maxima['groups']=max(maxima['groups'],len(groups));maxima['terms']=max(maxima['terms'],sum(map(len,groups)))
        assert modern_events==original['events'],'logical unfolding changed event time/order or dropped events'
        assert candidate['pending_tail_original_decisions']==[i for i,_ in pending]
        for index,step in enumerate(steps):
            expected=mapping.get(index)
            assert step['physical_flush']==expected and step['artificial_tail_unfold']==(expected is None)
        assert candidate['maximum_condition_groups']==maxima['groups'] and candidate['maximum_condition_terms']==maxima['terms']
        assert candidate['maximum_buffered_batches']==maxima['batches'] and candidate['maximum_compact_batch_payload_bytes']==maxima['bytes']
        actual=candidate['actual_delivered_snapshot'];real_events=[row for step in steps if not step['artificial_tail_unfold'] for row in step['new_modern_events']]
        assert actual['events']==real_events==original['events'][:len(real_events)]
        assert actual['native_operations']==original['native_operations']==list(registered.values())
        assert actual['native_tasks']==original['native_tasks']
        assert actual['decisions']==len(mapping)
        if not pending:assert actual==original
        summary[case]=dict(original_logical_steps=len(steps),candidate_physical_flushes=len(flushes),
            pending_tail_batches=len(pending),maximum_buffered_batches=maxima['batches'],
            maximum_compact_batch_payload_bytes=maxima['bytes'],native_operations=len(registered),
            modern_events_preserved=len(original['events']),full_reconstructed_snapshot_equal=True,
            actual_delivered_events=len(real_events),source_record_complete=original['runtime']['all_events_fired'])
    return dict(verified=True,network_executed=False,platform_modified=False,cases=summary,
        physical_interface_implemented=False,same_ns_native_scheduler_order_verified=False,
        representative_four_rank_call_complete=False,
        scope='saved front-end boundaries only; native suppressed Progress boundaries are not reconstructed')


def reject_tampering(report,source):
    edits={
        'changed_batch_time':lambda c:c['logical_steps'][1].update(now_ns=c['logical_steps'][1]['now_ns']+1),
        'lost_callbacks':lambda c:c['logical_steps'][1].update(callback_sha256='0'*64),
        'lost_boundary':lambda c:c['logical_steps'].pop(1),
        'changed_batch_order':lambda c:next(row for row in c['physical_flushes'] if len(row['original_batch_ids'])>1)['original_batch_ids'].reverse(),
        'false_intermediate_submission':lambda c:c['logical_steps'][1]['native_submissions'].append(65535),
        'future_condition':lambda c:c['physical_flushes'][0]['next_conditions'][0][0].update(operation=65535),
        'false_completion':lambda c:c.update(representative_four_rank_call_complete=True),
        'false_tail_wakeup':lambda c:c['pending_tail_original_decisions'].append(0),
        'changed_dynamic_state':lambda c:c['reconstructed_snapshot_after_explicit_tail_unfold']['native_tasks']['0'].update(role='tampered'),
    }
    rejected=[]
    for name,edit in edits.items():
        changed=deepcopy(report);edit(changed['cases']['four-routes']);assert changed!=report
        try:verify(changed,source)
        except AssertionError:rejected.append(name)
        else:raise AssertionError('tampered evidence accepted: '+name)
    return rejected


def main():
    p=argparse.ArgumentParser()
    for name in ('source','report','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();report=load(a.report);value=verify(report,a.source)
    value['tamper_rejections']=reject_tampering(report,a.source)
    a.output.write_text(json.dumps(value,indent=2)+'\n')


if __name__=='__main__':main()
