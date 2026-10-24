"""Actual wire/byte replay plus independent source-local grace reconstruction."""
from collections import Counter
from copy import deepcopy
from fixtures.selective_repeat.audit import audit as wire_audit


def grace_audit(r,w):
    t=r['persistent_transport'];g=t['nack_grace'];cfg=w['transport_recovery']['reordering']
    assert g['config']==cfg and len(g['events'])<=cfg['max_events']
    feedback=[e for e in t['selective_repeat']['events'] if e['kind']=='feedback_received']
    assert not any(e['kind']=='fast_retransmit' for e in t['selective_repeat']['events'])
    states={s['id']:dict(prefix=0,deadline_ns=-1,serviced=False,trigger_feedback_uid=-1) for s in w['streams']}
    seen=set();counts=Counter();arms=cancelled=duplicates=stale=0;retries=[]
    rows=t['recovery']['events'];columns=t['recovery']['columns']
    recovery=[dict(zip(columns,row)) for row in rows]
    last=-1
    for e in g['events']:
        now=e['time_ns'];sid=e['stream'];x=states[sid];kind=e['kind'];counts[kind]+=1
        assert now>=last;last=now
        assert e['before']==x
        assert e['snd_una']<=e['highest_sent_bytes']
        if kind=='feedback':
            f=feedback[len(seen)]
            assert (f['packet_uid'],f['time_ns'],f['stream'],f['cumulative_ack'],f['ip_protocol']==253)==(
                e['feedback_uid'],now,sid,e['cumulative_ack'],e['nack'])
            assert f['packet_uid'] not in seen;seen.add(f['packet_uid'])
            assert x['deadline_ns']<0 or now<=x['deadline_ns']
            ack=e['cumulative_ack'];old=deepcopy(x)
            if ack>x['prefix']:
                if x['deadline_ns']>=0:cancelled+=1
                x.update(prefix=ack,deadline_ns=-1,serviced=False,trigger_feedback_uid=-1)
            if ack<old['prefix']:stale+=1
            if e['nack'] and ack==x['prefix'] and not x['serviced']:
                if x['deadline_ns']<0:
                    x.update(deadline_ns=now+cfg['wait_ns'],trigger_feedback_uid=e['feedback_uid']);arms+=1
                else:duplicates+=1
            assert e['snd_una']==x['prefix']
        elif kind=='retry':
            assert x['deadline_ns']==now and x['trigger_feedback_uid'] in seen
            assert e['snd_una']==x['prefix']<e['highest_sent_bytes']
            assert e['rto_deadline_ns']<0 or e['rto_deadline_ns']>now
            retries.append(dict(time_ns=now,stream=sid,prefix=x['prefix']))
            x.update(deadline_ns=-1,serviced=True,trigger_feedback_uid=-1)
        elif kind in ('rto','rto_due'):
            if kind=='rto_due':assert x['deadline_ns']==now and 0<=e['rto_deadline_ns']<=now
            else:
                assert any(v['time_ns']==now and v['message_id']==sid and v['event'] in (2,4) for v in recovery)
            x.update(deadline_ns=-1,serviced=True,trigger_feedback_uid=-1)
        elif kind in ('close','stop_simulation'):
            if kind=='close':assert t['closed']
            else:assert t['recovery']['failed']
            x.update(deadline_ns=-1,trigger_feedback_uid=-1)
        else:raise AssertionError('unknown grace action')
        if kind!='feedback':assert e['feedback_uid']==e['cumulative_ack']==-1 and e['nack'] is False
        assert e['after']==x
    assert len(seen)==len(feedback)
    assert g['states']==[dict(x,stream=sid) for sid,x in sorted(states.items())]
    assert all(x['deadline_ns']==-1 and x['trigger_feedback_uid']==-1 for x in states.values())
    return dict(passed=True,events=dict(counts),arms=arms,progress_cancellations=cancelled,
        duplicate_nacks_did_not_extend=duplicates,stale_feedback=stale,retry_intents=retries)


def audit(r,w,c):
    result=dict(wire=wire_audit(r,w,c))
    if 'reordering' in w['transport_recovery']:result['grace']=grace_audit(r,w)
    else:assert 'nack_grace' not in r['persistent_transport']
    if c.get('faults'):assert r['fault_injection']['drop_counts']==[f['count'] for f in c['faults']]
    return result
