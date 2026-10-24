"""Independent byte/wire/queue replay of the declared two-flow partial failure.

Does not change passed/drained or pretend unfinished prefixes were completed.
"""
from collections import Counter,defaultdict,deque
import struct


def audit(r,w,c):
    t=r['persistent_transport'];cap=r['packet_capture'];sr=t['selective_repeat']
    assert not r['passed'] and not t['closed'] and not t['physical_drained']
    assert cap['complete'] and not cap['omitted_events'] and cap['selection']['nodes']==[]
    assert cap['selection']['start_ns']==0 and cap['selection']['end_ns']>=r['finish_ns']
    streams={x['id']:x for x in w['streams']};actual={x['id']:x for x in t['streams']}
    assert set(streams)==set(actual)=={2,8}
    total={sid:sum(x['bytes'] for x in w['work'] if x.get('stream')==sid) for sid in streams}
    assert total=={2:4000,8:4000}
    ports={(x['node'],x['port']):x for x in r['ports']}
    data={};generated={};feedback={};last_data={}
    for e in sr['events']:
        kind=e['kind'];sid=e['stream'];uid=e['packet_uid']
        if kind=='data_received':assert uid not in data;data[uid]=e;last_data[sid]=e
        elif kind=='feedback_generated':
            d=last_data[sid]
            assert e['time_ns']==d['time_ns'] and e['cumulative_ack']==d['receiver_prefix']
            assert all(e[k]==d[k] for k in ('sack_begin','sack_end','sack_valid'))
            assert uid not in generated;generated[uid]=e
        elif kind=='feedback_received':assert uid not in feedback;feedback[uid]=e
        else:raise AssertionError('unexpected selective event for partial byte input')
    masks={sid:bytearray(n) for sid,n in total.items()};known={sid:bytearray(n) for sid,n in total.items()}
    prefix=Counter();cumulative=Counter();held_bytes=Counter();peak=Counter();high=Counter();sent=Counter()
    held={sid:{} for sid in streams};source=[];seen_data=set();seen_feedback=set();seen_generated=set()
    inflight=defaultdict(deque);physical=Counter();area=Counter();occupancy=Counter();last=Counter();maxq=Counter()
    rx_times=defaultdict(list);ack_times=defaultdict(list);drops=0
    for raw in cap['events']:
        e=dict(zip(cap['columns'],raw));now=e['time_ns'];node=e['node'];port=e['port'];sid=e['message_id']
        proto=e['ip_protocol'];uid=e['packet_uid'];kind=e['event'];p=ports[node,port];peer=p['peer']
        identity=(uid,proto,e['sequence'],e['model_bytes'])
        if kind==0:inflight[node,peer,identity].append(now);physical['tx']+=1
        if kind in (1,2):
            assert inflight[peer,node,identity];when=inflight[peer,node,identity].popleft()
            assert now==when+p['delay_ns']+round(e['model_bytes']*8*10**9/p['rate_bps'])
            physical['rx' if kind==1 else 'drop']+=1
        if kind in (3,4) and node>=r['gpu_count']:
            key=node,port;area[key]+=occupancy[key]*(now-last[key]);last[key]=now
            occupancy[key]+=e['model_bytes'] if kind==3 else -e['model_bytes'];assert occupancy[key]>=0
            maxq[key]=max(maxq[key],occupancy[key])
        if proto==17 and kind==0 and node==streams[sid]['src']:
            begin=e['sequence'];end=begin+e['data_payload_bytes'];assert 0<=begin<end<=total[sid]
            assert not any(known[sid][begin:end])
            assert begin<cumulative[sid]+w['transport_recovery']['inflight_bytes']
            high[sid]=max(high[sid],end);sent[sid]+=end-begin
            source.append([now,sid,node,streams[sid]['dst'],port,begin,end-begin,uid])
        if proto==17 and kind==2 and node==streams[sid]['dst']:
            assert sid==2 and e['sequence']==0;drops+=1
        if proto==17 and kind==1 and node==streams[sid]['dst']:
            assert uid in data and uid not in seen_data;seen_data.add(uid);d=data[uid]
            begin=e['sequence'];end=begin+e['data_payload_bytes']
            assert 0<=begin<end<=total[sid];assert d['time_ns']==now and d['sequence']==begin
            if end>prefix[sid]:
                assert begin>=prefix[sid]
                if begin==prefix[sid]:
                    prefix[sid]=end
                    while prefix[sid] in held[sid]:
                        right=held[sid].pop(prefix[sid]);held_bytes[sid]-=right-prefix[sid];prefix[sid]=right
                elif begin not in held[sid]:
                    assert end-begin<=w['transport_recovery']['receive_buffer_bytes']-held_bytes[sid]
                    held[sid][begin]=end;held_bytes[sid]+=end-begin;peak[sid]=max(peak[sid],held_bytes[sid])
                else:assert held[sid][begin]==end
            assert d['sack_valid'] and (d['sack_begin'],d['sack_end'])==(begin,end)
            assert d['receiver_prefix']==prefix[sid] and d['reorder_bytes']==held_bytes[sid]
            rx_times[sid].append((now,prefix[sid]))
        if proto in (252,253) and kind==0 and node==streams[sid]['dst']:
            assert uid in generated;seen_generated.add(uid);g=generated[uid]
            assert g['time_ns']<=now and g['model_bytes']==e['model_bytes'] and g['cumulative_ack']==e['sequence']
        if proto in (252,253) and kind==1 and node==streams[sid]['src']:
            assert uid in feedback and uid in generated and uid not in seen_feedback;seen_feedback.add(uid)
            f=feedback[uid];g=generated[uid]
            assert f['time_ns']==now and f['cumulative_ack']==e['sequence']<=high[sid]
            assert f['extension_hex']==g['extension_hex'] and f['cumulative_ack']==g['cumulative_ack']
            magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(f['extension_hex']))
            assert magic==0x4d535231 and valid==1 and 0<=begin<end<=high[sid]
            assert (begin,end,True)==(f['sack_begin'],f['sack_end'],f['sack_valid'])
            known[sid][begin:end]=b'\1'*(end-begin);cumulative[sid]=max(cumulative[sid],e['sequence'])
            known[sid][:cumulative[sid]]=b'\1'*cumulative[sid];ack_times[sid].append((now,cumulative[sid]))
    assert not any(inflight.values()) and physical['tx']==physical['rx']+physical['drop']
    assert source==t['source_packets'] and set(data)==seen_data and set(feedback)==seen_feedback and set(generated)==seen_generated
    assert drops==2 and r['fault_injection']['drop_counts']==[2]
    for state in sr['states']:
        sid=state['stream'];assert state['receiver_prefix']==prefix[sid]==cumulative[sid]
        assert state['reorder_bytes']==held_bytes[sid] and state['peak_reorder_bytes']==peak[sid]
        assert state['held_ranges']==[[a,b] for a,b in sorted(held[sid].items())]
        expected=[];start=None
        for i,b in enumerate(known[sid]):
            if i<cumulative[sid]:continue
            if b and start is None:start=i
            if start is not None and (not b or i==total[sid]-1):
                expected.append([start,i if not b else i+1]);start=None
        assert state['source_sacked_ranges']==expected
        assert actual[sid]['source_payload_bytes']==sent[sid]
    assert prefix[2]==0 and held_bytes[2]==3000 and prefix[8]==4000 and not held_bytes[8]
    works={x['stream']:x for x in t['work']}
    assert works[2]['ready_ns']==works[2]['published_ns']==0 and works[2]['accepted_ns']==works[2]['acked_ns']==-1
    assert works[8]['accepted_ns']==min(n for n,p in rx_times[8] if p>=4000)
    assert works[8]['acked_ns']==min(n for n,p in ack_times[8] if p>=4000)
    assert r['completed_work']==1
    for key,p in ports.items():
        if key[0]<r['gpu_count']:continue
        assert occupancy[key]==p['queue_metrics']['final_bytes']==0
        assert area[key]==p['queue_metrics']['area_byte_ns'] and maxq[key]==p['queue_metrics']['peak_bytes']
    for sw in r['mmu_snapshot']['switches']:
        assert sw['shared_used_bytes']==0
        for p in sw['ports']:
            assert not any(v for key in ('ingress_bytes','headroom_used_bytes','egress_mmu_bytes','queue_bytes','pfc_paused') for v in p[key])
    isolation=t['source_isolation'];assert isolation['wire_idle'] and isolation['healthy_registered_work_drained']
    assert not isolation['failed_resources_released'] and isolation['terminal_feedback_packets']==0
    failures=t['recovery']['failures'];assert len(failures)==1 and failures[0]['stream']==2 and failures[0]['time_ns']==240000
    assert not any(row[1]==2 and row[0]>240000 for row in source)
    assert t['inflight_packets']==0 and sent[2]==5000 and sent[8]==4000
    return dict(passed=True,scope='partial execution verified as partial; held receive ranges and failed work remain',
        failed_prefix=0,failed_held_bytes=3000,healthy_prefix=4000,source_payload_bytes=dict(sent),
        failure_ns=240000,physical=dict(physical),actual_drops=drops,healthy_work_acked_ns=works[8]['acked_ns'])
