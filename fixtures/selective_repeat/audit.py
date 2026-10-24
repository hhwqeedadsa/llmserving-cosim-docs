"""Independent byte-mask and wire-feedback replay; no transport state-machine imports."""
import argparse
from collections import Counter,defaultdict,deque
import json
from pathlib import Path
import struct


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def audit(r,w,c):
    t=r['persistent_transport'];sr=t['selective_repeat'];capture=r['packet_capture']
    assert r['passed'] and r['drained'] and t['closed'] and t['physical_drained']
    assert capture['complete'] and not capture['omitted_events'] and capture['selection']['nodes']==[]
    assert capture['selection']['start_ns']==0 and capture['selection']['end_ns']>r['finish_ns']
    streams={s['id']:s for s in w['streams']};ports={(p['node'],p['port']):p for p in r['ports']}
    body_by_stream={s['id']:sum(x['bytes'] for x in w['work'] if x.get('stream')==s['id']) for s in w['streams']}
    masks={sid:bytearray(n) for sid,n in body_by_stream.items()};known={sid:bytearray(n) for sid,n in body_by_stream.items()}
    prefix=Counter();used=Counter();peak=Counter();cumulative=Counter();high=Counter()
    accepted=defaultdict(list);feedback=defaultdict(list);source=Counter();inflight=defaultdict(deque)
    generated={};received={};data_log={};fast=[];last_data={}
    for e in sr['events']:
        if e['kind']=='feedback_generated':
            d=last_data[e['stream']]
            assert e['time_ns']==d['time_ns'] and e['cumulative_ack']==d['receiver_prefix']
            assert all(e[k]==d[k] for k in ('sack_begin','sack_end','sack_valid'))
            generated[e['packet_uid']]=e
        elif e['kind']=='feedback_received':received[e['packet_uid']]=e
        elif e['kind']=='data_received':data_log[e['packet_uid']]=e;last_data[e['stream']]=e
        elif e['kind']=='fast_retransmit':fast.append(e)
        else:raise AssertionError('unknown selective event')
    # Packet UID is used together with physical edge and transmission occurrence.
    events=[dict(zip(capture['columns'],row)) for row in capture['events']]
    counts=Counter();seen_rx=set();seen_feedback=set();seen_generated=set();source_rows=[]
    last=Counter();occupancy=Counter();area=Counter();maxq=Counter()
    for e in events:
        sid=e['message_id'];proto=e['ip_protocol'];kind=e['event'];uid=e['packet_uid'];now=e['time_ns']
        n,p=e['node'],e['port'];port=ports[n,p];peer=port['peer'];packet=(uid,proto,e['sequence'],e['model_bytes'])
        if kind==0:inflight[n,peer,packet].append(now);counts['tx']+=1
        if kind in (1,2):
            assert inflight[peer,n,packet]
            sent=inflight[peer,n,packet].popleft()
            assert now==sent+port['delay_ns']+round(e['model_bytes']*8*10**9/port['rate_bps'])
            counts['rx' if kind==1 else 'physical_drop']+=1
        if kind in (3,4) and n>=r['gpu_count']:
            key=n,p;area[key]+=occupancy[key]*(now-last[key]);last[key]=now
            if kind==3:occupancy[key]+=e['model_bytes']
            else:occupancy[key]-=e['model_bytes']
            assert occupancy[key]>=0;maxq[key]=max(maxq[key],occupancy[key])
        if proto==17 and kind==0 and n==streams[sid]['src']:
            begin=e['sequence'];end=begin+e['data_payload_bytes']
            assert 0<=begin<end<=body_by_stream[sid]
            assert begin<cumulative[sid]+w['transport_recovery']['inflight_bytes']
            assert end<=cumulative[sid]+w['transport_recovery']['inflight_bytes']+c['payload_bytes']-1
            assert not any(known[sid][begin:end]),'source used already received selective/cumulative bytes'
            high[sid]=max(high[sid],end);source[sid]+=end-begin
            source_rows.append([now,sid,n,streams[sid]['dst'],p,begin,end-begin,uid])
        if proto==17 and kind==1 and n==streams[sid]['dst']:
            assert uid in data_log and uid not in seen_rx;seen_rx.add(uid);record=data_log[uid]
            begin=e['sequence'];end=begin+e['data_payload_bytes'];held=masks[sid]
            ok=True
            if end<=prefix[sid]:pass
            elif begin==prefix[sid]:
                prefix[sid]=end
                while prefix[sid]<len(held) and held[prefix[sid]]:
                    held[prefix[sid]]=0;used[sid]-=1;prefix[sid]+=1
            else:
                assert begin>prefix[sid]
                missing=sum(not b for b in held[begin:end])
                assert missing in (0,end-begin),'unexpected packet overlap'
                if missing and missing>w['transport_recovery']['receive_buffer_bytes']-used[sid]:ok=False
                elif missing:
                    held[begin:end]=b'\1'*(end-begin);used[sid]+=missing;peak[sid]=max(peak[sid],used[sid])
            assert record['time_ns']==now and record['sequence']==begin and record['sack_valid']==ok
            assert record['receiver_prefix']==prefix[sid] and record['reorder_bytes']==used[sid]
            assert (record['sack_begin'],record['sack_end'])==((begin,end) if ok else (0,0))
            accepted[sid].append((now,prefix[sid]))
        if proto in (252,253) and kind==0 and sid in streams and n==streams[sid]['dst']:
            assert uid in generated;g=generated[uid];seen_generated.add(uid)
            assert g['model_bytes']==e['model_bytes'] and g['cumulative_ack']==e['sequence']
            assert g['time_ns']<=now
        if proto in (252,253) and kind==1 and sid in streams and n==streams[sid]['src']:
            assert uid in received and uid in generated and uid not in seen_feedback;seen_feedback.add(uid)
            f=received[uid];g=generated[uid]
            assert f['time_ns']==now and f['cumulative_ack']==e['sequence'] and f['model_bytes']==e['model_bytes']
            magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(f['extension_hex']))
            assert magic==0x4d535231 and valid in (0,1)
            assert (begin,end,bool(valid))==(f['sack_begin'],f['sack_end'],f['sack_valid'])
            assert f['extension_hex']==g['extension_hex'] and f['cumulative_ack']==g['cumulative_ack']
            assert e['sequence']<=high[sid]
            if valid:assert begin<end<=high[sid];known[sid][begin:end]=b'\1'*(end-begin)
            else:assert begin==end==0
            cumulative[sid]=max(cumulative[sid],e['sequence']);known[sid][:cumulative[sid]]=b'\1'*cumulative[sid]
            feedback[sid].append((now,e['sequence']))
    assert not any(inflight.values()) and counts['tx']==counts['rx']+counts['physical_drop']
    assert source_rows==t['source_packets']
    assert seen_rx==set(data_log) and seen_feedback==set(received) and seen_generated==set(generated)
    assert len({(e['stream'],e['cumulative_ack']) for e in fast})==len(fast)
    for e in fast:assert e['packet_uid'] in received and e['ip_protocol']==253
    # A generated feedback may be physically dropped. Every received feedback
    # must have the exact serialized bytes from a real generated packet.
    work={x['id']:x for x in t['work']};local={x['id']:x for x in r['local_work']}
    times={}
    for x in w['work']:
        ready=max((times[d['work'],d['event']] for d in x['after']),default=0)
        if x['kind']=='local':
            y=local[x['id']];assert y['ready_ns']==ready
            assert y['completed_ns']==y['start_ns']+x['duration_ns']
            times[x['id'],'completed']=y['completed_ns']
        else:
            y=work[x['id']];sid=x['stream'];assert y['ready_ns']==ready and y['published_ns']>=ready
            assert y['accepted_ns']==min(n for n,prefix in accepted[sid] if prefix>=y['end'])
            assert y['acked_ns']==min(n for n,prefix in feedback[sid] if prefix>=y['end'])
            assert y['acked_ns']>=y['accepted_ns']
            times[x['id'],'accepted']=y['accepted_ns'];times[x['id'],'acked']=y['acked_ns']
    for state in sr['states']:
        sid=state['stream'];assert state['receiver_prefix']==prefix[sid]==cumulative[sid]==body_by_stream[sid]
        assert state['peak_reorder_bytes']==peak[sid] and not state['held_ranges'] and not state['source_sacked_ranges']
        assert state['reorder_bytes']==used[sid]==0 and state['native_window_bytes']==w['transport_recovery']['inflight_bytes']
        assert source[sid]==next(s['source_payload_bytes'] for s in t['streams'] if s['id']==sid)
    for key,port in ports.items():
        if key[0]<r['gpu_count']:continue
        assert occupancy[key]==port['queue_metrics']['final_bytes']==0
        assert area[key]==port['queue_metrics']['area_byte_ns'] and maxq[key]==port['queue_metrics']['peak_bytes']
    for sw in r['mmu_snapshot']['switches']:
        assert not sw['shared_used_bytes']
        for p in sw['ports']:
            assert not any(v for key in ('ingress_bytes','headroom_used_bytes','egress_mmu_bytes','queue_bytes','pfc_paused') for v in p[key])
    return dict(passed=True,source_packets=len(source_rows),source_payload_bytes=sum(source.values()),
        peak_reorder_bytes=dict(peak),physical=dict(counts),actual_feedback_received=len(received),
        fast_retransmits=len(fast),ack_extension_model_bytes=sorted({e['model_bytes'] for e in received.values()}))


def main():
    p=argparse.ArgumentParser()
    for name in ('stage','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();results={}
    for path in sorted((a.stage/'network').glob('*/result.json')):
        if path.parent.name.startswith('default-'):continue
        name=path.parent.name
        results[name]=audit(load(path),load(a.stage/'inputs'/(name+'-work.json')),load(a.stage/'inputs'/(name+'-protocol.json')))
    assert results
    save(a.output,dict(passed=True,cases=results,scope='independent finite-byte oracle and actual wire/queue/callback replay; not performance evidence'))
    print(json.dumps(dict(passed=True,cases=len(results))))


if __name__=='__main__':main()
