"""Account all receive-witness queues using actual on-port service, not a rerun."""
import argparse
from bisect import bisect_right
from collections import Counter,defaultdict,deque
import json
from pathlib import Path


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def summarize(result,summary):
    t=result['persistent_transport'];c=result['packet_capture'];events=[dict(zip(c['columns'],row)) for row in c['events']]
    source=[dict(zip(t['source_packet_columns'],row)) for row in t['source_packets']]
    attempts=Counter();identities={};first={};retry_outcomes=defaultdict(Counter)
    streams={v['id']:v for v in t['streams']}
    delivered={e['packet_uid']:e['time_ns'] for e in events if e['event']==1 and e['ip_protocol']==17 and e['node']==streams[e['message_id']]['dst']}
    dropped={e['packet_uid'] for e in events if e['event'] in (2,5) and e['ip_protocol']==17}
    for packet in source:
        sid=packet['stream'];seq=packet['sequence'];attempts[sid,seq]+=1
        key=(packet['packet_uid'],sid,seq);assert key not in identities
        identities[key]=dict(stream=sid,attempt=attempts[sid,seq],payload=packet['payload_bytes'],source_tx_ns=packet['time_ns'])
        segment=sid,seq,packet['payload_bytes']
        if segment not in first:first[segment]=packet
        else:
            original=first[segment];uid=original['packet_uid']
            if uid in dropped:state='initial_copy_lost'
            elif delivered[uid]<=packet['time_ns']:state='initial_copy_received_before_retry'
            else:state='initial_copy_still_inflight_at_retry'
            retry_outcomes[str(sid)][state]+=packet['payload_bytes']
    assert sum(p['payload_bytes'] for p in first.values())==summary['unique_payload_bytes']
    assert sum(sum(v.values()) for v in retry_outcomes.values())==summary['tx_payload_bytes']-summary['unique_payload_bytes']
    def category(e):
        if e['ip_protocol']!=17:return 'control:'+str(e['ip_protocol'])
        origin=identities[e['packet_uid'],e['message_id'],e['sequence']]
        return str(origin['stream'])+(' retransmission' if origin['attempt']>1 else ' first transmission')
    targets=defaultdict(list)
    for work in summary['writes']:
        for hop in work['witness']['queues']:
            targets[hop['node'],hop['port'],work['witness']['packet_uid'],work['witness']['sequence']].append((work['work'],hop))
    fifo=defaultdict(deque);ahead={};tx=defaultdict(list);ports={(p['node'],p['port']):p for p in result['ports']}
    for e in events:
        node=e['node'];port=e['port'];key=(node,port)
        if e['event']==0:
            duration=round(e['model_bytes']*8*10**9/ports[key]['rate_bps'])
            tx[key].append(dict(begin=e['time_ns'],end=e['time_ns']+duration,category=category(e),uid=e['packet_uid']))
        if e['ip_protocol']!=17 or node<result['gpu_count']:continue
        packet=(e['packet_uid'],e['message_id'],e['sequence'])
        if e['event']==3:
            for wid,hop in targets[node,port,e['packet_uid'],e['sequence']]:
                assert e['time_ns']==hop['enqueue_ns'];counts=Counter();repeated=0
                for row in fifo[key]:
                    counts[category(row)]+=row['model_bytes']
                    if identities[row['packet_uid'],row['message_id'],row['sequence']]['attempt']>1:repeated+=row['model_bytes']
                ahead[wid,node,port]=dict(data_model_bytes=sum(counts.values()),retransmission_model_bytes=repeated,by_stream_attempt_model_bytes=dict(counts))
            fifo[key].append(e)
        if e['event']==4:
            old=fifo[key].popleft();assert (old['packet_uid'],old['message_id'],old['sequence'])==packet
    assert not any(fifo.values())
    for rows in tx.values():assert all(a['end']<=b['begin'] for a,b in zip(rows,rows[1:])), 'overlapping physical port transmissions'
    ends={key:[row['end'] for row in rows] for key,rows in tx.items()}
    rates=t['source_rate_capture'];rate_index={(row[3],row[1]):dict(zip(rates['columns'],row)) for row in rates['samples'] if row[2]==0}
    output=[]
    for work in summary['writes']:
        witness=work['witness'];hops=[]
        for hop in witness['queues']:
            key=hop['node'],hop['port'];start=hop['enqueue_ns'];stop=hop['dequeue_ns'];service=Counter();owners=Counter()
            for row in tx[key][bisect_right(ends[key],start):]:
                if row['begin']>=stop:break
                overlap=max(0,min(stop,row['end'])-max(start,row['begin']))
                service[row['category']]+=overlap;owners[row['category']]+=bool(overlap)
            busy=sum(service.values());wait=stop-start;assert 0<=busy<=wait
            hops.append(dict(node=key[0],port=key[1],peer=hop['peer'],enqueue_ns=start,dequeue_ns=stop,
                wait_ns=wait,busy_service_ns=busy,nonservice_wait_ns=wait-busy,
                service_by_stream_attempt_ns=dict(service),service_packet_counts=dict(owners),
                ahead_at_enqueue=ahead[work['work'],*key]))
        assert sum(h['wait_ns'] for h in hops)==witness['queue_wait_ns']
        rate=rate_index[witness['packet_uid'],work['stream']]
        assert rate['time_ns']==witness['source_tx_ns'] and rate['sequence']==witness['sequence']
        output.append(dict(work=work['work'],stream=work['stream'],name=work['name'],graph_id=work['graph_id'],
            ready_ns=work['ready_ns'],accepted_ns=work['accepted_ns'],acked_ns=work['acked_ns'],
            witness_source_rate_sample=rate,hops=hops))
    return dict(writes=output,all_witness_queues_accounted=True,
        repeated_payload_by_initial_copy_outcome={sid:dict(v) for sid,v in retry_outcomes.items()},
        retry_classification_scope='posthoc exact original byte-segment outcome; successful initial delivery was not necessarily known to source when retrying',
        scope='actual serialization overlapping each witness wait, including residual frame and control; does not prove cross-flow counterfactual gain')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();s=a.stage;analysis=load(a.analysis);index=load(s/'inputs/observed/index.json');cases={}
    for name,summary in analysis['cases'].items():
        result=load((s/'inputs/baseline'/name if name in index['saved_cases'] else s/'network'/name)/'result.json')
        cases[name]=summarize(result,summary)
    save(a.output,dict(passed=True,cases=cases,all_writes_included=True,network_executions=0,
        warning='first/retransmission classification is actual source attempt, not hindsight avoidability; declared independent DCQCN is uncalibrated'))


if __name__=='__main__':main()
