"""Independent edge coverage and old/new aggregate acceptance, no native execution."""
import argparse
from copy import deepcopy
from pathlib import Path
from fixtures.nack_grace.audit import audit as byte_audit,grace_audit
from fixtures.live_ecmp.audit import audit as route_audit
from fixtures.transaction_selective_payload.audit import audit as transaction_audit
from fixtures.nack_grace_edges.partial_wire import audit as partial_audit
from fixtures.nack_grace_edges.wire_fifo import audit as fifo_audit
from fixtures.nack_grace_edges.stage import load,save,sha


def one(s,name):
    r=load(s/'network'/name/'result.json');w=load(s/'inputs'/(name+'-work.json'));c=load(s/'inputs'/(name+'-protocol.json'))
    if name=='transaction':
        report=dict(payload_and_effects=transaction_audit(r,w,c))
        transport=r['transactions']['transport']
        # Alias the real transport container, without changing any success,
        # drain, feedback or sequence value.
        report['grace']=grace_audit(dict(persistent_transport=transport),dict(w,streams=transport['streams']))
        assert r['fault_injection']['drop_counts']==[1]
    elif name=='source-isolated':report=dict(partial_wire=partial_audit(r,w,c),grace=grace_audit(r,w))
    else:
        report=byte_audit(r,w,c)
        if name=='transient-detour':report['routes']=route_audit(load(s/'topology/topology.json'),c,w,r)
    report["physical_service"]=fifo_audit(r)
    return r,w,c,report


def verify(s):
    assert all(sha(s/p)==h for p,h in load(s/'inputs.json')['files'].items())
    results={};raw={}
    for name in load(s/'inputs.json')['conditions']:
        r,w,c,report=one(s,name);results[name]=report;raw[name]=(r,w,c)
    detour=results['transient-detour']['grace'];r,w,c=raw['transient-detour']
    assert detour['progress_cancellations']>0 and not detour['retry_intents']
    t=r['persistent_transport'];assert sum(x['source_payload_bytes'] for x in t['streams'])==65536
    cancellations=[e for e in t['nack_grace']['events'] if e['kind']=='feedback' and
        e['cumulative_ack']>e['before']['prefix'] and e['before']['deadline_ns']>=0]
    matured=[e for e in cancellations if e['before']['deadline_ns']<r['finish_ns']]
    assert matured and all(e['time_ns']<e['before']['deadline_ns'] for e in cancellations)
    assert not any(e['kind'] in ('retry','rto') for e in t['nack_grace']['events'])
    # Past cancelled deadlines actually elapse before simulation stop, so
    # absence of retries cannot be explained solely by an early Stop().
    results['transient-detour']['cancellation_witness']=dict(count=len(cancellations),deadlines_elapsed_before_finish=len(matured),
        first=dict(time_ns=cancellations[0]['time_ns'],old_deadline_ns=cancellations[0]['before']['deadline_ns'],
            old_prefix=cancellations[0]['before']['prefix'],new_prefix=cancellations[0]['cumulative_ack']))
    # Bind the first cancelled timer to the actual overtaking data packet,
    # its generated NACK, and the original hole-filling packet's physical path.
    first=cancellations[0];sr=t['selective_repeat']['events']
    nack_uid=first['before']['trigger_feedback_uid']
    generated=next(e for e in sr if e['kind']=='feedback_generated' and e['packet_uid']==nack_uid)
    overtaker=next(e for e in sr if e['kind']=='data_received' and e['time_ns']==generated['time_ns'] and e['receiver_prefix']==0)
    initial=next(e for e in sr if e['kind']=='data_received' and e['sequence']==0)
    cap=r['packet_capture'];events=[dict(zip(cap['columns'],e)) for e in cap['events']]
    ports={(e['node'],e['port']):e for e in r['ports']}
    def path(uid):
        rows=[e for e in events if e['event']==0 and e['ip_protocol']==17 and e['packet_uid']==uid]
        assert rows and all(ports[e['node'],e['port']]['peer']==rows[i+1]['node'] for i,e in enumerate(rows[:-1]))
        return [e['node'] for e in rows]+[ports[rows[-1]['node'],rows[-1]['port']]['peer']]
    assert overtaker['sequence']>0 and overtaker['time_ns']<initial['time_ns']
    old_path=path(initial['packet_uid']);new_path=path(overtaker['packet_uid'])
    assert len(old_path)>len(new_path) and not any(e['event'] in (2,5) for e in events)
    results['transient-detour']['mechanism_witness']=dict(original_uid=initial['packet_uid'],
        original_sequence=0,original_rx_ns=initial['time_ns'],original_actual_path=old_path,
        overtaker_uid=overtaker['packet_uid'],overtaker_sequence=overtaker['sequence'],
        overtaker_rx_ns=overtaker['time_ns'],overtaker_actual_path=new_path,
        actual_nack_uid=nack_uid,nack_generated_ns=generated['time_ns'],
        actual_ack_received_ns=first['time_ns'],cancelled_deadline_ns=first['before']['deadline_ns'])
    for name in ('rto-preempts','source-isolated'):
        r,w,c=raw[name];t=r['persistent_transport'];events=t['nack_grace']['events']
        superseded=[e for e in events if e['kind']=='rto' and e['before']['deadline_ns']>=0]
        assert len(superseded)==1 and superseded[0]['time_ns']==80000<superseded[0]['before']['deadline_ns']
        assert not any(e['kind']=='retry' for e in events)
        assert results[name]['grace']['duplicate_nacks_did_not_extend']>0
        results[name]['rto_cancellation_witness']=dict(time_ns=80000,cancelled_deadline_ns=superseded[0]['before']['deadline_ns'])
    r,w,c=raw['transaction'];t=r['transactions']['transport']
    assert results['transaction']['grace']['retry_intents']
    assert sum(x['timeout_retransmissions'] for x in t['recovery']['states'])==0
    mutated=[]
    for case,mode in [('transient-detour','cancel_deadline'),('transient-detour','feedback_uid'),
                      ('rto-preempts','rto_kept_pending'),('source-isolated','pretend_healthy_prefix'),
                      ('source-isolated','release_failed_work'),('transient-detour','wrong_fifo_uid'),('transaction','second_apply'),('transaction','wrong_atomic_value')]:
        r,w,c=deepcopy(raw[case]);transport=r.get('persistent_transport',r.get('transactions',{}).get('transport'))
        if mode=='cancel_deadline':
            e=next(e for e in transport['nack_grace']['events'] if e['kind']=='feedback' and e['before']['deadline_ns']>=0)
            e['after']['deadline_ns']=e['before']['deadline_ns']
        if mode=='feedback_uid':transport['nack_grace']['events'][0]['feedback_uid']+=100000
        if mode=='rto_kept_pending':
            e=next(e for e in transport['nack_grace']['events'] if e['kind']=='rto');e['after']['deadline_ns']=e['before']['deadline_ns']
        if mode=='pretend_healthy_prefix':next(x for x in transport['selective_repeat']['states'] if x['stream']==2)['receiver_prefix']=4000
        if mode=='release_failed_work':next(x for x in transport['work'] if x['stream']==2)['acked_ns']=240000
        if mode=='wrong_fifo_uid':
            cap=r['packet_capture'];columns=cap['columns'];event=columns.index('event');proto=columns.index('ip_protocol');node=columns.index('node')
            row=next(row for row in cap['events'] if row[event]==4 and row[proto]==17 and row[node]>=r['gpu_count'])
            row[columns.index('packet_uid')]+=100000
        if mode=='second_apply':r['transactions']['target_events'].append(deepcopy(r['transactions']['target_events'][0]))
        if mode=='wrong_atomic_value':next(x for x in r['transactions']['operations'] if 'returned_value' in x)['returned_value']+=1
        rejected=False
        try:
            if case=='transaction':transaction_audit(r,w,c);grace_audit(dict(persistent_transport=transport),dict(w,streams=transport['streams']))
            elif case=='source-isolated':partial_audit(r,w,c);grace_audit(r,w)
            else:byte_audit(r,w,c)
            fifo_audit(r)
        except (AssertionError,KeyError,IndexError,ValueError):rejected=True
        assert rejected,mode
        mutated.append(mode)
    return dict(passed=True,platform_core_gates_passed=True,network_attempts=4,successful_applications=3,
        expected_partial_failures=1,cases=results,mutations_rejected=mutated,
        exact_same_ns_persistent_network_verified=False,online_transaction_session_verified=False,
        scope='bounded classical source grace functionality; not optimization benefit, modern GIN, calibrated timing or paper contribution')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,verify(a.stage));print('four edge cases independently verified')

if __name__=='__main__':main()
