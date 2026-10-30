"""Independent reconstruction from interleaved native observations and decisions.

No platform consumer, stored policy decisions or future ACK is used to choose.
"""
import json
from pathlib import Path

def prefix(c,r,f,path):
    specs=c['whole_call_replication']['calls'];steps=c['workload']['decode_steps'];option=c['whole_call_replication']
    guarded=option['schema_version']=='moe-whole-call-replication/5'
    retired={};skipped={};transcript=iter(f['transcript']);timer=[];pending_timer=None;ticket=0;submits={}
    bindings={};confirmations={};decisions=[];source_sequence=-1;source_time=-1
    specs_by_group={q['primary_group']:q for q in specs}
    def record(kind,now,active):timer.append(dict(sequence=len(timer),time_ns=now,kind=kind,deadline_ns=active[0],ticket=active[1]))
    for line in (path/'native.stdout.log').read_text().splitlines(keepends=True):
        if not line.startswith('MOE_RDMA_EVENT '):continue
        assert line.endswith('\n');e=json.loads(line[len('MOE_RDMA_EVENT '):])
        if e['type']=='OBSERVE':
            assert e['sequence']>source_sequence and e['time_ns']>=source_time
            source_sequence=e['sequence'];source_time=e['time_ns']
            if e['kind']=='graph_retired':
                assert e['graph_id'] in submits and e['graph_id'] not in retired
                retired[e['graph_id']]=e
            if guarded and e['kind'] in ('message_send_post','message_send_complete'):
                graph=e['graph_id'];group=int(graph.split('.')[0][1:]);spec=specs_by_group.get(group)
                if spec and spec['source_ack_timeout_ns'] is not None:
                    mid=e['message'];assert bindings[mid]==(spec['id'],graph,e['rank'])
                    if e['kind']=='message_send_post':
                        assert mid not in confirmations
                        confirmations[mid]=dict(call=spec['id'],graph=graph,rank=e['rank'],post_ns=e['time_ns'],ack_ns=None)
                    else:
                        assert mid in confirmations and confirmations[mid]['ack_ns'] is None
                        confirmations[mid]['ack_ns']=e['time_ns']
            continue
        assert e['type']=='DECIDE';row=next(transcript);assert row['event']==e;reply=row['reply'];now=e['now_ns']
        assert now>=source_time
        if e['wakeup_ns'] is not None:
            assert pending_timer and pending_timer[0]==now==e['wakeup_ns'];record('fired',now,pending_timer);pending_timer=None
        assert pending_timer is None or pending_timer[0]>=now
        expected={};wake=[]
        for call in specs:
            primary=call['primary_group'];replica=call['replica_group'];deadline=call['hedge_after_ns']
            timeout=call['source_ack_timeout_ns'] if guarded else None
            if f'g{primary}.w0' not in submits:
                assert now==0;expected[primary]=0
            ids=sorted(mid for mid,m in confirmations.items() if m['call']==call['id'] and m['ack_ns'] is None)
            overdue=[mid for mid in ids if timeout is not None and confirmations[mid]['post_ns']+timeout<=now]
            reason='already_started';next_check=None
            if f'g{replica}.w0' not in submits and replica not in skipped:
                final=f'g{primary}.w{steps-1}'
                if final in retired:
                    assert retired[final]['time_ns']==now<=deadline;skipped[replica]=now;reason='primary_complete'
                elif now>=deadline or overdue:
                    assert now<=deadline
                    if not guarded:assert e['wakeup_ns']==deadline==now
                    expected[replica]=0;reason='fallback' if now==deadline else 'source_timeout'
                else:
                    reason='poll'
                    candidates=[deadline]
                    if timeout is not None:candidates.extend([now+timeout]+[confirmations[mid]['post_ns']+timeout for mid in ids])
                    next_check=min(candidates);assert next_check>now;wake.append(next_check)
            elif replica in skipped:reason='primary_complete'
            if timeout is not None:
                decisions.append(dict(sequence=len(decisions),time_ns=now,call=call['id'],reason=reason,
                    observed_sequence=source_sequence,pending_messages=ids,overdue_messages=overdue,next_deadline_ns=next_check))
            for index in (primary,replica):
                submitted=[step for step in range(steps) if f'g{index}.w{step}' in submits]
                if submitted and len(submitted)<steps and f'g{index}.w{submitted[-1]}' in retired:
                    assert submitted==list(range(len(submitted)))
                    assert retired[f'g{index}.w{submitted[-1]}']['time_ns']==now
                    expected[index]=len(submitted)
        paths=[f'g{index}.w{expected[index]}' for index in sorted(expected)]
        target=min(wake) if wake else None
        assert reply['wake_at_ns']==target and [Path(p).name for p in reply['submissions']]==paths
        for graph in paths:
            assert graph not in submits;submits[graph]=now
            group=int(graph.split('.')[0][1:]);spec=specs_by_group.get(group)
            if guarded and spec and spec['source_ack_timeout_ns'] is not None:
                wave=json.loads((path/'graphs'/graph/'wave.json').read_bytes())
                for m in wave['messages']:
                    assert m['id'] not in bindings;bindings[m['id']]=(spec['id'],graph,m['src'])
                assert len(bindings)<=option['max_guard_messages']
        if pending_timer and target!=pending_timer[0]:record('cancelled',now,pending_timer);pending_timer=None
        if target is not None and pending_timer is None:
            pending_timer=(target,ticket);ticket+=1;record('armed',now,pending_timer)
    assert next(transcript,None) is None and pending_timer is None and set(submits)==set(retired)
    assert r['service']['control_wakeup']['events']==timer
    assert r['service']['control_wakeup']['pending_deadline_ns'] is None
    if guarded:
        assert len(decisions)<=option['max_guard_events']
        assert f['whole_call_replication']['schema_version']=='moe-whole-call-report/4'
        assert f['whole_call_replication']['source_ack_guard']==dict(schema_version='moe-source-ack-guard-report/1',
            decisions=decisions,messages={str(mid):m for mid,m in sorted(confirmations.items())})
        assert len(confirmations)==len(bindings) and all(m['ack_ns'] is not None for m in confirmations.values())
    calls=f['whole_call_replication']['calls'];assert set(calls)=={call['id'] for call in specs}
    assert f['whole_call_replication']['decode_steps']==steps
    for spec in specs:
        call=calls[spec['id']];done=[]
        for role in ('primary','replica'):
            a=call['attempts'][role];index=spec[role+'_group'];first=f'g{index}.w0'
            assert a['group_index']==index and a['graph_id']==first
            if first in submits:
                expected=[dict(graph_id=f'g{index}.w{step}',step=step,submitted_ns=submits[f'g{index}.w{step}'],
                    finished_ns=retired[f'g{index}.w{step}']['time_ns']) for step in range(steps)]
                assert a['waves']==expected and a['submitted_ns']==expected[0]['submitted_ns'] and a['finished_ns']==expected[-1]['finished_ns']
                assert all(b['submitted_ns']==aa['finished_ns'] for aa,b in zip(expected,expected[1:]))
                done.append(retired[expected[-1]['graph_id']])
            else:
                assert role=='replica' and a['submitted_ns'] is a['finished_ns'] is None and a['waves']==[]
                assert a['skipped_ns']==skipped[index]
        winner=min(done,key=lambda e:e['sequence']);assert call['result_ready_ns']==winner['time_ns']
        group=call['attempts'][call['winner']]['group_index']
        assert winner['graph_id']==f'g{group}.w{steps-1}'
    return retired,submits,skipped
