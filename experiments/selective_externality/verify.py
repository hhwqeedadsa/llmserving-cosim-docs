"""Independent routing, physical-hop, FIFO, prefix and witness checks (no analyzer import)."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import hashlib
import json
import struct
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
    sr=t.get('selective_repeat')
    assert bool(sr)==(config['transport_recovery']['mode']=='selective_repeat/1')
    if sr:assert sr['receive_buffer_bytes']==262144 and sr['inflight_bytes']==261145
    budgets={sid:sum(w['bytes'] for w in config['work'] if w['stream']==sid) for sid in streams}
    assert max(budgets.values())<261145
    masks={sid:bytearray(n) for sid,n in budgets.items()};known={sid:bytearray(n) for sid,n in budgets.items()}
    used=Counter();peaks=Counter();high=Counter();generated={};received={};data_records={}
    if sr:
        for e in sr['events']:
            if e['kind']=='feedback_generated':generated[e['packet_uid']]=e
            if e['kind']=='feedback_received':received[e['packet_uid']]=e
            if e['kind']=='data_received':data_records[e['packet_uid']]=e
    seen_data=set();seen_feedback=set();seen_generated=set()
    assert len(expected)==len(t['work'])==r['completed_work']
    assert all(w['ready_ns']==w['published_ns']==0 for w in t['work'])
    ports={(p['node'],p['port']):p for p in r['ports']}
    back={(p['node'],p['peer']):p['port'] for p in r['ports']}
    outstanding=defaultdict(deque); source=defaultdict(list); rx=defaultdict(list); ack=defaultdict(list)
    queue_bytes=Counter(); area=Counter(); peak=Counter(); last=Counter(); fifo=defaultdict(deque)
    attempt=Counter(); by_uid={}; queue_witnesses={}; physical=[0,0,0]; src_seen=[]
    accepted_prefix=Counter(); acked_prefix=Counter()
    total=int(r['finish_ns']); events=[dict(zip(c['columns'],row)) for row in c['events']]
    headers=c['wire_headers'];assert len(headers['event_header_indices'])==len(events)
    reverse_ips={(0x0b000001+s['dst']*32,0x0b000001+s['src']*32):sid for sid,s in streams.items()}
    for event,hi in zip(events,headers['event_header_indices']):
        h=dict(zip(headers['columns'],headers['headers'][hi]))
        assert h['packet_uid']==event['packet_uid'] and h['ip_protocol']==event['ip_protocol']
        event['mapped_stream']=reverse_ips[h['ip_src'],h['ip_dst']] if event['ip_protocol']==255 else event['message_id']
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
            end=seq+event['data_payload_bytes']
            assert 0<=seq<end<=budgets[sid]<261145
            if sr:
                assert not any(known[sid][seq:end]),'selective source retransmitted already-known bytes'
                high[sid]=max(high[sid],end)
            source[sid].append(event)
            src_seen.append([now,sid,n,streams[sid]['dst'],p,seq,event['data_payload_bytes'],uid])
        if proto==17 and kind==1 and n==streams[sid]['dst']:
            end=seq+event['data_payload_bytes']
            if sr:
                assert uid in data_records and uid not in seen_data;seen_data.add(uid)
                before=accepted_prefix[sid]
                if end>before:
                    missing=sum(not b for b in masks[sid][max(seq,before):end])
                    assert used[sid]+missing<=262144
                    masks[sid][max(seq,before):end]=b'\1'*(end-max(seq,before));used[sid]+=missing
                    while accepted_prefix[sid]<budgets[sid] and masks[sid][accepted_prefix[sid]]:
                        masks[sid][accepted_prefix[sid]]=0;used[sid]-=1;accepted_prefix[sid]+=1
                peaks[sid]=max(peaks[sid],used[sid])
                e=data_records[uid]
                assert e['time_ns']==now and e['sequence']==seq and e['sack_valid']
                assert (e['sack_begin'],e['sack_end'])==(seq,end)
                assert e['receiver_prefix']==accepted_prefix[sid] and e['reorder_bytes']==used[sid]
            elif seq==accepted_prefix[sid]:accepted_prefix[sid]+=event['data_payload_bytes']
        if proto in (252,253) and kind==0 and n==streams[sid]['dst']:
            assert size==(50 if sr else 48)
            if sr:
                assert uid in generated;seen_generated.add(uid)
                g=generated[uid];assert g['cumulative_ack']==seq and g['time_ns']<=now and g['model_bytes']==size
                magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(g['extension_hex']))
                assert magic==0x4d535231 and valid==1
                assert (begin,end,True)==(g['sack_begin'],g['sack_end'],g['sack_valid'])
                data=next(x for x in data_records.values() if x['stream']==sid and x['time_ns']==g['time_ns'])
                assert (begin,end)==(data['sack_begin'],data['sack_end']) and seq==data['receiver_prefix']
        if proto in (252,253) and kind==1 and n==streams[sid]['src']:
            acked_prefix[sid]=max(acked_prefix[sid],seq)
            if sr:
                assert uid in generated and uid in received and uid not in seen_feedback;seen_feedback.add(uid)
                e=received[uid];g=generated[uid]
                assert e['time_ns']==now and e['cumulative_ack']==seq and e['model_bytes']==size
                assert e['extension_hex']==g['extension_hex']
                magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(e['extension_hex']))
                assert magic==0x4d535231 and valid==1 and 0<=begin<end<=high[sid] and seq<=high[sid]
                assert (begin,end,True)==(e['sack_begin'],e['sack_end'],e['sack_valid'])
                known[sid][begin:end]=b'\1'*(end-begin);known[sid][:acked_prefix[sid]]=b'\1'*acked_prefix[sid]

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
        if proto in (252,253) and kind==1 and n==streams[sid]['src']: ack[sid].append(event)
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
        byte_set=set()
        for e in rx[sid]:
            prior=cursor
            if sr:
                byte_set.update(range(e['sequence'],e['sequence']+e['data_payload_bytes']))
                while cursor in byte_set:byte_set.remove(cursor);cursor+=1
            elif e['sequence']==cursor:cursor+=e['data_payload_bytes']
            if cursor>prior:
                delivered.append(e)
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
        stream_events=[e for e in events if e['mapped_stream']==sid]
        actual_edges={str(proto):sorted({(e['node'],ports[e['node'],e['port']]['peer']) for e in stream_events
                                         if e['event']==0 and e['ip_protocol']==proto}) for proto in (17,252,253,255)}
        assert s['edges']=={k:[list(edge) for edge in edges] for k,edges in actual_edges.items()}
        assert s['dropped_by_protocol']==dict(Counter(str(e['ip_protocol']) for e in stream_events if e['event'] in (2,5)))
        assert s['direct_edge_seen']==any((8,10) in edges or (10,8) in edges for edges in actual_edges.values())
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
    assert summary['all_work_ack_ns']==max(w['acked_ns'] for w in t['work'])
    assert summary['all_work_accepted_ns']==max(w['accepted_ns'] for w in t['work'])
    assert summary['tx_payload_bytes']==sum(row[6] for row in src_seen)
    assert summary['source_model_bytes_by_protocol']=={str(proto):sum(e['model_bytes'] for e in events
        if e['event']==0 and e['node']<r['gpu_count'] and e['ip_protocol']==proto) for proto in (17,252,253,255)}
    if sr:
        assert seen_data==set(data_records) and seen_feedback==set(received) and seen_generated==set(generated)
        for state in sr['states']:
            sid=state['stream']
            assert state['receiver_prefix']==budgets[sid]==acked_prefix[sid]
            assert state['native_window_bytes']==261145
            assert not used[sid] and not state['held_ranges'] and not state['source_sacked_ranges']
            assert state['peak_reorder_bytes']==peaks[sid] and not state['rejected_packets']
        assert summary['sum_per_stream_peak_reorder_bytes']==sum(peaks.values())
    return dict(window_never_binding=True,selective_feedback_received=len(received),source_packets=len(src_seen),physical_tx=physical[0],physical_rx=physical[1],physical_drop=physical[2],work=len(expected))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','inputs','runs','analysis','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists()
    truth=load(a.inputs/'truth/routes.json');observed=a.inputs/'observed'
    index=load(observed/'index.json');analysis=load(a.analysis)
    for name,digest in index['files'].items():assert hashlib.sha256((observed/name).read_bytes()).hexdigest()==digest
    expected=[]
    assert len(truth['routes'])==len(truth['manifest']['files'])==4
    for src,(entry,row) in enumerate(zip(truth['manifest']['files'],truth['routes'])):
        original=a.repository/entry['file']['path']
        assert hashlib.sha256(original.read_bytes()).hexdigest()==row['sha256']==entry['file']['sha256']
        tokens=load(original)[0]['4'][:8]
        assert tokens==row['tokens']
        for experts in tokens:
            for dst in range(4):
                if dst!=src and any(dst*64<=e<(dst+1)*64 for e in experts):
                    expected.append(dict(id=len(expected),stream=src*4+dst,bytes=7168*2+128,after=[]))
    gbn=load(observed/'gbn-work.json');selective=load(observed/'selective-work.json')
    assert expected==gbn['work']==selective['work'] and len(expected)==74
    stripped=deepcopy(selective)
    stripped['transport_recovery']['mode']='go_back_n/1'
    assert stripped['transport_recovery'].pop('inflight_bytes')==261145
    assert stripped['transport_recovery'].pop('receive_buffer_bytes')==262144
    assert stripped==gbn
    checks={}
    for case in index['cases']:
        r=load(a.runs/case['name']/'result.json');config=load(observed/case['work'])
        checks[case['name']]=check_case(r,config,analysis['cases'][case['name']])
    # Reject mutations of actual evidence without running another network.
    original=load(a.runs/'failed-selective/result.json');ss=analysis['cases']['failed-selective']
    variants=[]
    bad=deepcopy(original);bad['persistent_transport']['work'][0]['acked_ns']+=1
    variants.append(('ack_callback',bad,deepcopy(ss)))
    bad=deepcopy(original);columns=bad['packet_capture']['columns']
    e=next(e for e in bad['packet_capture']['events'] if e[columns.index('event')]==1)
    e[columns.index('time_ns')]+=1;variants.append(('physical_arrival',bad,deepcopy(ss)))
    bad=deepcopy(original)
    e=next(e for e in bad['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')
    e['extension_hex']='00'+e['extension_hex'][2:];variants.append(('selective_wire_feedback',bad,deepcopy(ss)))
    bad=deepcopy(original)
    e=next(e for e in bad['persistent_transport']['selective_repeat']['events'] if e['kind']=='data_received')
    e['receiver_prefix']+=1;variants.append(('reorder_prefix',bad,deepcopy(ss)))
    altered=deepcopy(ss)
    q=next(q for s in altered['streams'] for q in s['witness']['queues'] if q['data_bytes_ahead_by_stream'])
    q['data_bytes_ahead_by_stream']={};variants.append(('queue_owner',deepcopy(original),altered))
    rejected=[]
    for label,bad,s in variants:
        try:check_case(bad,selective,s)
        except (AssertionError,KeyError,StopIteration):rejected.append(label)
        else:raise AssertionError('undetected mutation: '+label)
    # Recompute each reported difference directly from independently checked cases.
    by_name=analysis['cases']
    for row in analysis['comparisons']:
        key=row['timer'];sid=row['stream']
        healthy=next(s for s in by_name['healthy-'+key]['streams'] if s['stream']==sid)
        failed=next(s for s in by_name['failed-'+key]['streams'] if s['stream']==sid)
        assert row['ack_delta_ns']==failed['acked_ns']-healthy['acked_ns']
        assert row['accepted_delta_ns']==failed['accepted_ns']-healthy['accepted_ns']
        assert row['extra_tx_payload_bytes']==failed['tx_payload_bytes']-healthy['tx_payload_bytes']
        category=('direct_drop' if failed['dropped_by_protocol'] else
                  'baseline_uses_failed_edge' if healthy['direct_edge_seen'] else
                  'no_drop_off_failed_edge_changed_paths' if failed['edges']!=healthy['edges'] else
                  'no_drop_off_failed_edge_same_paths')
        assert row['category']==category
    save(a.output,dict(passed=True,independent_checks=checks,rejected_mutations=rejected,
        original_files_independently_hashed_and_selected=True,actual_network_executions=4,
        scope='source-backed finite window, actual physical packets, bounded SR feedback, queues and callbacks; no application speedup or hardware calibration'))
    print(json.dumps(dict(passed=True,cases=4,rejected=rejected)))


if __name__=='__main__':main()

