"""Independent routing, physical-hop, FIFO, prefix and witness checks (no analyzer import)."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')


def check_case(r, config, summary):
    t=r['persistent_transport']; c=r['packet_capture']
    assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
    assert c['complete'] and c['selection']['nodes']==[] and c['selection']['start_ns']==0
    assert c['selection']['end_ns']>r['finish_ns'] and not c['omitted_events']
    streams={s['id']:s for s in config['streams']}; native={s['id']:s for s in t['streams']}
    expected={w['id']:w for w in config['work']}
    assert len(expected)==len(t['work'])==r['completed_work']
    assert all(w['ready_ns']==w['published_ns']==0 for w in t['work'])
    ports={(p['node'],p['port']):p for p in r['ports']}
    back={(p['node'],p['peer']):p['port'] for p in r['ports']}
    outstanding=defaultdict(deque); source=defaultdict(list); rx=defaultdict(list); ack=defaultdict(list)
    queue_bytes=Counter(); area=Counter(); peak=Counter(); last=Counter(); fifo=defaultdict(deque)
    attempt=Counter(); by_uid={}; queue_witnesses={}; physical=[0,0,0]; src_seen=[]
    accepted_prefix=Counter(); acked_prefix=Counter()
    total=int(r['finish_ns']); events=[dict(zip(c['columns'],row)) for row in c['events']]
    for event in events:
        n,p,kind,now=(event[k] for k in ('node','port','event','time_ns'))
        sid,proto,seq,uid,size=(event[k] for k in ('message_id','ip_protocol','sequence','packet_uid','model_bytes'))
        assert 0<=now<=total
        port=ports[n,p];peer=port['peer']
        packet=(uid,proto,seq,size)
        if kind==0:
            outstanding[n,peer,packet].append(now);physical[0]+=1
        elif kind in (1,2):
            assert outstanding[peer,n,packet], ('unmatched_physical_arrival',event)
            sent=outstanding[peer,n,packet].popleft()
            other=ports[peer,back[peer,n]]
            flight=round(size*8*10**9/other['rate_bps'])+other['delay_ns']
            assert now==sent+flight, ('physical_arrival_time',now,sent,flight)
            physical[kind]+=1
        if kind in (3,4) and n>=r['gpu_count']:
            key=n,p
            area[key]+=queue_bytes[key]*(now-last[key]);last[key]=now
            if kind==3:
                queue_bytes[key]+=size
                assert event['egress_queue_bytes']==queue_bytes[key]
            else:
                assert event['egress_queue_bytes']==queue_bytes[key]
                queue_bytes[key]-=size
            assert queue_bytes[key]>=0
            peak[key]=max(peak[key],queue_bytes[key])
        if proto==17 and kind==0 and n==streams[sid]['src']:
            attempt[sid,seq]+=1; by_uid[sid,uid,seq]=attempt[sid,seq]
            source[sid].append(event)
            src_seen.append([now,sid,n,streams[sid]['dst'],p,seq,event['data_payload_bytes'],uid])
        if proto==17 and kind==1 and n==streams[sid]['dst'] and seq==accepted_prefix[sid]:
            accepted_prefix[sid]+=event['data_payload_bytes']
        if proto==252 and kind==1 and n==streams[sid]['src']:acked_prefix[sid]=max(acked_prefix[sid],seq)
        if proto==17 and kind==3:
            ahead=Counter(); repeats=Counter(); received_ahead=Counter(); acked_ahead=Counter()
            for queued in fifo[n,p]:
                osid=queued['message_id'];ahead[str(osid)]+=queued['model_bytes']
                if by_uid[osid,queued['packet_uid'],queued['sequence']]>1:
                    repeats[str(osid)]+=queued['model_bytes']
                    end=queued['sequence']+queued['data_payload_bytes']
                    if end<=accepted_prefix[osid]:received_ahead[str(osid)]+=queued['model_bytes']
                    if end<=acked_prefix[osid]:acked_ahead[str(osid)]+=queued['model_bytes']
            fifo[n,p].append(event)
            queue_witnesses[n,p,uid,seq]=dict(enqueue_ns=now,ahead=dict(ahead),repeated=dict(repeats),
                                           received=dict(received_ahead),acked=dict(acked_ahead))
        if proto==17 and kind==4:
            first=fifo[n,p].popleft()
            assert first['packet_uid']==uid and first['sequence']==seq
            queue_witnesses[n,p,uid,seq]['dequeue_ns']=now
        if proto==17 and kind==1 and n==streams[sid]['dst']: rx[sid].append(event)
        if proto==252 and kind==1 and n==streams[sid]['src']: ack[sid].append(event)
    assert not any(outstanding.values()) and not any(fifo.values())
    assert physical[0]==physical[1]+physical[2]
    assert src_seen==t['source_packets']
    summaries={s['stream']:s for s in summary['streams']}
    work_by_stream=defaultdict(list)
    for w in t['work']:
        assert expected[w['id']]['stream']==w['stream'] and expected[w['id']]['bytes']==w['bytes']
        work_by_stream[w['stream']].append(w)
    for sid, declared in streams.items():
        writes=sorted(work_by_stream[sid],key=lambda w:w['end'])
        cursor=0; accepted={}; delivered=[]
        for e in rx[sid]:
            if e['sequence']==cursor:
                cursor+=e['data_payload_bytes'];delivered.append(e)
                for w in writes:
                    if w['end']<=cursor and w['id'] not in accepted: accepted[w['id']]=e['time_ns']
        assert cursor==native[sid]['submitted_bytes']==native[sid]['snd_una']
        assert native[sid]['qp_creations']==1 and len(writes)<declared['queue_depth']
        previous=0
        for w in writes:
            assert w['begin']==previous and w['end']==previous+w['bytes'];previous=w['end']
            assert accepted[w['id']]==w['accepted_ns']
            ack_time=min(e['time_ns'] for e in ack[sid] if e['sequence']>=w['end'])
            assert ack_time==w['acked_ns']
        s=summaries[sid]; witness=s['witness'];last_write=writes[-1]
        assert s['accepted_ns']==last_write['accepted_ns'] and s['acked_ns']==last_write['acked_ns']
        assert s['tx_payload_bytes']==sum(e['data_payload_bytes'] for e in source[sid])==native[sid]['source_payload_bytes']
        last_rx=next(e for e in delivered if e['time_ns']==last_write['accepted_ns'])
        assert last_rx['packet_uid']==witness['packet_uid'] and last_rx['sequence']==witness['sequence']
        source_packet=next(e for e in source[sid] if e['packet_uid']==witness['packet_uid'] and e['sequence']==witness['sequence'])
        assert source_packet['time_ns']==witness['source_tx_ns'] and last_rx['time_ns']==witness['destination_rx_ns']
        assert sum(q['wait_ns'] for q in witness['queues'])==witness['queue_wait_ns']
        route=[];transit=0
        for e in events:
            if e['ip_protocol']==17 and e['message_id']==sid and e['packet_uid']==witness['packet_uid'] and e['sequence']==witness['sequence'] and e['event']==0:
                pp=ports[e['node'],e['port']];route.append((e['node'],pp['peer']))
                transit+=round(e['model_bytes']*8*10**9/pp['rate_bps'])+pp['delay_ns']
        assert route[0][0]==declared['src'] and route[-1][1]==declared['dst']
        assert all(x[1]==y[0] for x,y in zip(route,route[1:]))
        assert route[1:]==[(q['node'],q['peer']) for q in witness['queues']]
        assert witness['nonqueue_transit_ns']==transit
        assert last_rx['time_ns']-source_packet['time_ns']==transit+witness['queue_wait_ns']
        for q in witness['queues']:
            evidence=queue_witnesses[q['node'],q['port'],q['packet_uid'],q['sequence']]
            assert q['enqueue_ns']==evidence['enqueue_ns'] and q['dequeue_ns']==evidence['dequeue_ns']
            assert q['wait_ns']==q['dequeue_ns']-q['enqueue_ns']
            assert q['data_bytes_ahead_by_stream']==evidence['ahead']
            assert q['repeated_model_bytes_ahead_by_stream']==evidence['repeated']
            assert q['repeated_already_received_before_enqueue']==evidence['received']
            assert q['repeated_already_acked_before_enqueue']==evidence['acked']
            for ex in q['stale_ahead_examples']:
                ss=ex['stream'];uid=ex['packet_uid'];seq=ex['sequence']
                own_tx=next(e for e in source[ss] if e['packet_uid']==uid and e['sequence']==seq)
                end=seq+own_tx['data_payload_bytes']
                own_enq=next(e for e in events if e['event']==3 and e['node']==q['node'] and e['port']==q['port']
                             and e['packet_uid']==uid and e['sequence']==seq and e['message_id']==ss)
                assert ex['source_tx_ns']==own_tx['time_ns'] and ex['owner_enqueue_ns']==own_enq['time_ns']
                assert ex['source_ack_ns']==min(e['time_ns'] for e in ack[ss] if e['sequence']>=end)
                assert ex['source_ack_ns']<=q['enqueue_ns'] and ex['owner_enqueue_ns']<=q['enqueue_ns']
                assert ex['payload_bytes']==own_tx['data_payload_bytes'] and ex['model_bytes']==own_tx['model_bytes']
    for key,port in ports.items():
        if key[0]<r['gpu_count']:continue
        assert queue_bytes[key]==port['queue_metrics']['final_bytes']==0
        assert area[key]==port['queue_metrics']['area_byte_ns'] and peak[key]==port['queue_metrics']['peak_bytes']
    for sw in r['mmu_snapshot']['switches']:
        assert sw['shared_used_bytes']==0
        for port in sw['ports']:
            for name in ('ingress_bytes','headroom_used_bytes','egress_mmu_bytes','queue_bytes','pfc_paused'):
                assert not any(port[name])
    if 'link_failures' in r:
        lf=r['link_failures'];assert lf['pending_events']==0 and lf['route_epoch']==1
        assert [(e['kind'],e['time_ns']) for e in lf['events']]==[('link_down',4000),('routes_updated',16000)]
        for e in events:
            if e['event']==0 and (e['node'],ports[e['node'],e['port']]['peer']) in ((8,10),(10,8)):
                assert e['time_ns']<4000
    return dict(source_packets=len(src_seen),physical_tx=physical[0],physical_rx=physical[1],physical_drop=physical[2],work=len(expected))


def main():
    p=argparse.ArgumentParser()
    for name in ('inputs','runs','analysis','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    truth=load(a.inputs/'truth/routes.json'); observed=a.inputs/'observed'
    index=load(observed/'index.json');analysis=load(a.analysis)
    for name,digest in index['files'].items():assert hashlib.sha256((observed/name).read_bytes()).hexdigest()==digest
    expected=[]
    for src,row in enumerate(truth['routes']):
        for token,experts in enumerate(row['tokens']):
            # Independent expert/rank counting; one remote token copy despite multiple experts.
            for dst in range(4):
                if dst!=src and any(dst*64<=e<(dst+1)*64 for e in experts):
                    expected.append(dict(id=len(expected),stream=src*4+dst,bytes=7168*2+128,after=[]))
    fixed=load(observed/'fixed-work.json');karn=load(observed/'karn-work.json')
    assert expected==fixed['work']==karn['work'] and len(expected)==74
    k=deepcopy(karn);del k['transport_recovery']['rtt_estimator'];assert k==fixed
    checks={}
    for case in index['cases']:
        r=load(a.runs/case['name']/'result.json');config=load(observed/case['work'])
        checks[case['name']]=check_case(r,config,analysis['cases'][case['name']])
    # Independent real-source evidence mutations; no simulation reruns.
    original=load(a.runs/'failed-fixed/result.json');config=fixed;s=analysis['cases']['failed-fixed'];rejected=[]
    bad=deepcopy(original);bad['persistent_transport']['work'][0]['acked_ns']+=1
    variants=[('ack_callback',bad,deepcopy(s))]
    bad=deepcopy(original);columns=bad['packet_capture']['columns']
    e=next(e for e in bad['packet_capture']['events'] if e[columns.index('event')]==1)
    e[columns.index('time_ns')]+=1;variants.append(('physical_arrival',bad,deepcopy(s)))
    altered=deepcopy(s);next(x for x in altered['streams'] if x['stream']==8)['witness']['queues'][3]['repeated_model_bytes_ahead_by_stream']={}
    variants.append(('retransmission_queue_owner',deepcopy(original),altered))
    for label,bad,ss in variants:
        try:check_case(bad,config,ss)
        except (AssertionError,KeyError,StopIteration):rejected.append(label)
        else:raise AssertionError('undetected mutation: '+label)
    save(a.output,dict(passed=True,independent_checks=checks,rejected_mutations=rejected,
        original_route_identity='Preparation verifies original file SHA256; checker reconstructs the frozen selected routes.',
        scope='physical packet timing, FIFO owner and prefix callback checks, not hardware calibration or innovation proof'))
    print(json.dumps(dict(passed=True,cases=4,rejected=rejected)))


if __name__=='__main__':main()
