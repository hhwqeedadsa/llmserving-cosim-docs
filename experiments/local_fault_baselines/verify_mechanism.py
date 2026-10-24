"""Recheck queue owners/serialization and first-copy outcomes from raw events."""
import argparse
from collections import Counter,defaultdict,deque
from copy import deepcopy
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def check(result,summary,reported):
    t=result['persistent_transport'];c=result['packet_capture'];events=[dict(zip(c['columns'],row)) for row in c['events']]
    source=[dict(zip(t['source_packet_columns'],row)) for row in t['source_packets']]
    counts=Counter();attempt={};first={};outcomes=defaultdict(Counter);streams={s['id']:s for s in t['streams']}
    arrival={e['packet_uid']:e['time_ns'] for e in events if e['event']==1 and e['ip_protocol']==17 and e['node']==streams[e['message_id']]['dst']}
    lost={e['packet_uid'] for e in events if e['event'] in (2,5) and e['ip_protocol']==17}
    for p in source:
        sid=p['stream'];seq=p['sequence'];counts[sid,seq]+=1;attempt[p['packet_uid']]=counts[sid,seq]
        key=sid,seq,p['payload_bytes']
        if key not in first:first[key]=p
        else:
            original=first[key]['packet_uid']
            state='initial_copy_lost' if original in lost else 'initial_copy_received_before_retry' if arrival[original]<=p['time_ns'] else 'initial_copy_still_inflight_at_retry'
            outcomes[str(sid)][state]+=p['payload_bytes']
    assert reported['repeated_payload_by_initial_copy_outcome']=={sid:dict(v) for sid,v in outcomes.items()}
    assert sum(p['payload_bytes'] for p in first.values())==summary['unique_payload_bytes']
    assert sum(sum(v.values()) for v in outcomes.values())==summary['tx_payload_bytes']-summary['unique_payload_bytes']
    def label(e):
        return str(e['message_id'])+(' retransmission' if attempt[e['packet_uid']]>1 else ' first transmission') if e['ip_protocol']==17 else 'control:'+str(e['ip_protocol'])
    ports={(p['node'],p['port']):p for p in result['ports']};physical=defaultdict(list);fifo=defaultdict(deque);snapshots={}
    targets={(h['node'],h['port'],w['witness']['packet_uid'],w['witness']['sequence']) for w in summary['writes'] for h in w['witness']['queues']}
    for e in events:
        key=e['node'],e['port']
        if e['event']==0:physical[key].append(e)
        if e['node']<result['gpu_count'] or e['ip_protocol']!=17:continue
        if e['event']==3:
            identity=(*key,e['packet_uid'],e['sequence'])
            if identity in targets:
                ahead=Counter()
                for queued in fifo[key]:ahead[label(queued)]+=queued['model_bytes']
                snapshots[identity]=dict(data_model_bytes=sum(ahead.values()),
                    retransmission_model_bytes=sum(v for k,v in ahead.items() if k.endswith(' retransmission')),
                    by_stream_attempt_model_bytes=dict(ahead))
            fifo[key].append(e)
        if e['event']==4:
            queued=fifo[key].popleft();assert queued['packet_uid']==e['packet_uid'] and queued['sequence']==e['sequence']
    assert not any(fifo.values())
    rate=t['source_rate_capture'];rate_rows=[dict(zip(rate['columns'],row)) for row in rate['samples']]
    rate_index={(v['packet_uid'],v['stream']):v for v in rate_rows if v['event']==0}
    declared={w['work']:w for w in summary['writes']};assert {w['work'] for w in reported['writes']}==set(declared)
    hops_checked=0
    for w in reported['writes']:
        original=declared[w['work']];witness=original['witness']
        assert w['witness_source_rate_sample']==rate_index[witness['packet_uid'],w['stream']]
        assert len(w['hops'])==len(witness['queues'])
        for hop,target in zip(w['hops'],witness['queues']):
            for key in ('node','port','peer','enqueue_ns','dequeue_ns','wait_ns'):assert hop[key]==target[key]
            key=hop['node'],hop['port'];duration=hop['wait_ns'];busy=Counter();number=Counter()
            for packet in physical[key]:
                start=packet['time_ns'];end=start+round(packet['model_bytes']*8*10**9/ports[key]['rate_bps'])
                amount=max(0,min(end,hop['dequeue_ns'])-max(start,hop['enqueue_ns']))
                if amount:busy[label(packet)]+=amount;number[label(packet)]+=1
            # Omit zero-overlap categories; these are not actual service.
            assert {k:v for k,v in hop['service_by_stream_attempt_ns'].items() if v}==dict(busy)
            assert {k:v for k,v in hop['service_packet_counts'].items() if v}==dict(number)
            assert hop['busy_service_ns']==sum(busy.values()) and hop['nonservice_wait_ns']==duration-sum(busy.values())>=0
            assert hop['ahead_at_enqueue']==snapshots[*key,witness['packet_uid'],witness['sequence']]
            hops_checked+=1
    return dict(passed=True,writes=len(declared),hops=hops_checked,repeated_payload_bytes=sum(sum(v.values()) for v in outcomes.values()),
        nonservice_wait_ns=sum(h['nonservice_wait_ns'] for w in reported['writes'] for h in w['hops']))


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','mechanism','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();s=a.stage;analysis=load(a.analysis);mechanism=load(a.mechanism)
    idx=load(s/'inputs/observed/index.json');reports={}
    for name,summary in analysis['cases'].items():
        r=load((s/'inputs/baseline'/name if name in idx['saved_cases'] else s/'network'/name)/'result.json')
        reports[name]=check(r,summary,mechanism['cases'][name])
    r=load(s/'network/live-instant/result.json');summary=analysis['cases']['live-instant'];original=mechanism['cases']['live-instant'];rejected=[]
    for name in ('service_owner','queue_occupancy','retry_outcome','source_rate'):
        bad=deepcopy(original)
        if name=='service_owner':
            hop=next(h for w in bad['writes'] for h in w['hops'] if h['service_by_stream_attempt_ns'])
            key=next(iter(hop['service_by_stream_attempt_ns']));hop['service_by_stream_attempt_ns'][key]+=1
        elif name=='queue_occupancy':bad['writes'][0]['hops'][0]['ahead_at_enqueue']['data_model_bytes']+=1
        elif name=='source_rate':bad['writes'][0]['witness_source_rate_sample']['rate_bps']+=1
        else:bad['repeated_payload_by_initial_copy_outcome']['12']['initial_copy_still_inflight_at_retry']+=1
        try:check(r,summary,bad)
        except AssertionError:rejected.append(name)
        else:raise AssertionError('undetected mechanism tamper '+name)
    save(a.output,dict(passed=True,cases=reports,rejected_tamper_classes=rejected,network_executions=0,
        scope='physical queue-service and posthoc delivery outcome facts, no source clairvoyance or counterfactual savings'))


if __name__=='__main__':main()
