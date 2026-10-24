"""Independent packet/feedback/MRT1/memory replay of the declared finite fixtures.

Standard library only. Does not import a platform decoder or transport model.
Actual received packets supply bytes; input operations supply expected intent.
"""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import json
from pathlib import Path
import struct


def need(ok, why):
    if not ok:
        raise AssertionError(why)


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def carrier(r,w,c):
    t=r['transactions']['transport'];sr=t['selective_repeat'];cap=r['packet_capture']
    need(r['passed'] and r['drained'] and t['closed'] and t['physical_drained'],'drain')
    need(cap['complete'] and not cap['omitted_events'] and cap['selection']['nodes']==[] and
         cap['selection']['start_ns']==0 and cap['selection']['end_ns']>r['finish_ns'],'capture coverage')
    streams={}
    for x in w['connections']:
        for rev in (0,1):
            streams[2*x['id']+rev]=dict(src=x['dst'] if rev else x['src'],dst=x['src'] if rev else x['dst'],
                sn=x['destination_nic'] if rev else x['source_nic'],dn=x['source_nic'] if rev else x['destination_nic'],
                port=x['response_port'] if rev else x['request_port'])
    packets=[dict(zip(t['source_packet_columns'],x)) for x in t['source_packets']]
    need(len(packets)==len(t['source_payload_packets']),'source payload coverage')
    raw={};source_uid={};total=Counter()
    for p,b in zip(packets,t['source_payload_packets']):
        need(all(p[k]==b[k] for k in ('time_ns','stream','sequence')),'source row identity')
        data=bytes.fromhex(b['hex']);need(len(data)==p['payload_bytes'] and data,'source length')
        need(p['packet_uid'] not in raw,'source UID uniqueness')
        raw[p['packet_uid']]=data;source_uid[p['packet_uid']]=p
        total[p['stream']]=max(total[p['stream']],p['sequence']+len(data))
    # Repeated transmissions must carry the same byte at the same stream offset.
    transmitted={sid:bytearray(total[sid]) for sid in streams};covered={sid:bytearray(total[sid]) for sid in streams}
    for p in packets:
        sid=p['stream'];pos=p['sequence'];data=raw[p['packet_uid']]
        for i,b in enumerate(data,pos):
            need(not covered[sid][i] or transmitted[sid][i]==b,'retransmitted bytes changed')
            transmitted[sid][i]=b;covered[sid][i]=1
    need(all(all(v) for v in covered.values()),'source byte holes')
    generated={};received={};data_log={};deliveries=defaultdict(list);fast=[];last_data={}
    for e in sr['events']:
        sid=e['stream'];kind=e['kind']
        if kind=='data_received':
            need(e['packet_uid'] not in data_log,'duplicate RX record');data_log[e['packet_uid']]=e;last_data[sid]=e
        elif kind=='payload_delivered':deliveries[sid].append(e)
        elif kind=='feedback_generated':
            d=last_data[sid]
            need(e['time_ns']==d['time_ns'] and e['cumulative_ack']==d['receiver_prefix'] and
                 all(e[k]==d[k] for k in ('sack_begin','sack_end','sack_valid')),'generated feedback prefix')
            need(e['packet_uid'] not in generated,'duplicate feedback');generated[e['packet_uid']]=e
        elif kind=='feedback_received':
            need(e['packet_uid'] not in received,'duplicate feedback RX');received[e['packet_uid']]=e
        elif kind=='fast_retransmit':fast.append(e)
        else:raise AssertionError('unknown selective event')
    ports={(p['node'],p['port']):p for p in r['ports']};wire=cap['wire_headers']
    need(len(wire['event_header_indices'])==len(cap['events']),'wire coverage')
    inflight=defaultdict(deque);prefix=Counter();used=Counter();peak=Counter();held=defaultdict(dict)
    known={sid:bytearray(total[sid]) for sid in streams};cum=Counter();high=Counter()
    expected=defaultdict(list);accepted=defaultdict(list);feedback=defaultdict(list)
    seen_rx=set();seen_tx=set();seen_gen=set();seen_feedback=set();counts=Counter();source=Counter()
    occupancy=Counter();area=Counter();last=Counter();maxq=Counter()
    for i,row in enumerate(cap['events']):
        e=dict(zip(cap['columns'],row));h=dict(zip(wire['columns'],wire['headers'][wire['event_header_indices'][i]]))
        uid=e['packet_uid'];sid=e['message_id'];proto=e['ip_protocol'];kind=e['event'];now=e['time_ns'];n=e['node'];p=e['port']
        port=ports[n,p];peer=port['peer'];identity=uid,proto,e['sequence'],e['model_bytes']
        need(h['packet_uid']==uid and h['ip_protocol']==proto,'actual wire identity')
        if kind==0:inflight[n,peer,identity].append(now);counts['tx']+=1
        if kind in (1,2):
            need(bool(inflight[peer,n,identity]),'physical RX without TX');sent=inflight[peer,n,identity].popleft()
            need(now==sent+port['delay_ns']+round(e['model_bytes']*8*10**9/port['rate_bps']),'link service time')
            counts['rx' if kind==1 else 'drop']+=1
        if kind in (3,4) and n>=r['gpu_count']:
            key=n,p;area[key]+=occupancy[key]*(now-last[key]);last[key]=now
            occupancy[key]+=e['model_bytes'] if kind==3 else -e['model_bytes']
            need(occupancy[key]>=0,'queue underflow');maxq[key]=max(maxq[key],occupancy[key])
        if proto==17:
            s=streams[sid];need(h['ports_present'] and h['source_port']==h['destination_port']==s['port'],'actual data ports')
            need(uid in raw,'unrecorded source packet');sp=source_uid[uid];body=raw[uid]
            need(e['sequence']==sp['sequence'] and e['data_payload_bytes']==len(body),'physical data range')
            begin=e['sequence'];end=begin+len(body)
            if kind==0 and n==s['src']:
                need(uid not in seen_tx and now==sp['time_ns'] and p==s['sn'] and sp['src']==n and sp['dst']==s['dst'] and sp['nic']==p,'source NIC/TX')
                seen_tx.add(uid);need(not any(known[sid][begin:end]),'retransmit bytes already known received')
                need(begin<cum[sid]+w['transport_recovery']['inflight_bytes'] and
                     end<=cum[sid]+w['transport_recovery']['inflight_bytes']+c['payload_bytes']-1,'sender window')
                high[sid]=max(high[sid],end);source[sid]+=len(body)
            if kind==1 and n==s['dst']:
                need(p==s['dn'] and uid in data_log and uid not in seen_rx,'endpoint RX');seen_rx.add(uid)
                ok=True;before=prefix[sid];chunks=[]
                if end<=before:pass
                elif begin==before:
                    chunks.append((begin,uid));prefix[sid]=end
                    while prefix[sid] in held[sid]:
                        old_uid=held[sid].pop(prefix[sid]);pos=prefix[sid];chunks.append((pos,old_uid))
                        prefix[sid]+=len(raw[old_uid]);used[sid]-=len(raw[old_uid])
                else:
                    need(begin>before,'partial overlap unsupported by fixture oracle')
                    if begin not in held[sid]:
                        if used[sid]+len(body)>w['transport_recovery']['receive_buffer_bytes']:ok=False
                        else:held[sid][begin]=uid;used[sid]+=len(body);peak[sid]=max(peak[sid],used[sid])
                d=data_log[uid]
                need(d['time_ns']==now and d['sequence']==begin and d['sack_valid']==ok and
                     d['receiver_prefix']==prefix[sid] and d['reorder_bytes']==used[sid] and
                     (d['sack_begin'],d['sack_end'])==((begin,end) if ok else (0,0)),'receive prefix/cache')
                for pos,chunk_uid in chunks:expected[sid].append((now,pos,chunk_uid,raw[chunk_uid].hex()))
                accepted[sid].append((now,prefix[sid]))
        if proto in (252,253) and sid in streams:
            s=streams[sid];need(h['ports_present'] and h['source_port']==h['destination_port']==s['port'],'feedback ports')
            if kind==0 and n==s['dst']:
                need(uid in generated and uid not in seen_gen and p==s['dn'],'feedback TX');seen_gen.add(uid);g=generated[uid]
                need(g['time_ns']<=now and g['model_bytes']==e['model_bytes'] and g['cumulative_ack']==e['sequence'],'feedback TX prefix')
            if kind==1 and n==s['src']:
                need(uid in received and uid in generated and uid not in seen_feedback and p==s['sn'],'feedback RX');seen_feedback.add(uid)
                f=received[uid];g=generated[uid]
                need(f['time_ns']==now and f['cumulative_ack']==e['sequence'] and f['model_bytes']==e['model_bytes']==50,'feedback RX bytes/time')
                magic,begin,end,valid=struct.unpack('!IIII',bytes.fromhex(f['extension_hex']))
                need(magic==0x4d535231 and valid in (0,1) and
                     (begin,end,bool(valid))==(f['sack_begin'],f['sack_end'],f['sack_valid']) and
                     f['extension_hex']==g['extension_hex'] and f['cumulative_ack']==g['cumulative_ack'],'actual feedback extension')
                need(e['sequence']<=high[sid],'ACK of unsent bytes')
                if valid:
                    need(begin<end<=high[sid],'SACK of unsent bytes');known[sid][begin:end]=b'\1'*(end-begin)
                else:need(begin==end==0,'invalid empty feedback')
                cum[sid]=max(cum[sid],e['sequence']);known[sid][:cum[sid]]=b'\1'*cum[sid]
                feedback[sid].append((now,e['sequence']))
    need(not any(inflight.values()) and counts['tx']==counts['rx']+counts['drop'],'physical drain')
    need(seen_tx==set(raw) and seen_rx==set(data_log) and seen_gen==set(generated) and seen_feedback==set(received),'record coverage')
    for sid in streams:
        actual=[(d['time_ns'],d['sequence'],d['packet_uid'],d['hex']) for d in deliveries[sid]]
        need(actual==expected[sid],'actual ordered delivered payload provenance')
        need(prefix[sid]==cum[sid]==total[sid] and not held[sid] and not used[sid],'prefix drain')
    need(len(sr['states'])==len(streams),'selective state coverage')
    for state in sr['states']:
        sid=state['stream'];need(state['receiver_prefix']==total[sid] and state['peak_reorder_bytes']==peak[sid] and
            not state['held_ranges'] and not state['source_sacked_ranges'] and state['reorder_bytes']==0 and
            state['native_window_bytes']==w['transport_recovery']['inflight_bytes'],'final selective state')
    for s in t['streams']:
        sid=s['id'];need(s['qp_creations']==1 and s['submitted_bytes']==s['published_bytes']==s['snd_una']==total[sid] and
            s['source_payload_bytes']==source[sid],'persistent stream state')
    for x in t['work']:
        sid=x['stream'];end=x['end'];need(x['bytes']==end-x['begin'] and x['accepted_ns']==min(n for n,p in accepted[sid] if p>=end) and
            x['acked_ns']==min(n for n,p in feedback[sid] if p>=end),'work receive/ACK callback')
    for key,p in ports.items():
        if key[0]>=r['gpu_count']:need(occupancy[key]==p['queue_metrics']['final_bytes']==0 and area[key]==p['queue_metrics']['area_byte_ns'] and maxq[key]==p['queue_metrics']['peak_bytes'],'queue area/peak/drain')
    need(len({(e['stream'],e['cumulative_ack']) for e in fast})==len(fast),'repeated fast recovery')
    for e in fast:need(e['packet_uid'] in received and e['ip_protocol']==253,'fast recovery requires actual NACK')
    # Decode complete frames from the bytes actually delivered, including cross-packet headers/bodies.
    frames=defaultdict(list)
    for sid in streams:
        blob=b'';times=[]
        for now,pos,uid,hex_ in expected[sid]:
            need(pos==len(blob),'delivery hole');b=bytes.fromhex(hex_);blob+=b;times.extend([now]*len(b))
        pos=0
        while pos<len(blob):
            need(len(blob)-pos>=32,'incomplete MRT1 header')
            magic,verb,seq,address,key,size=struct.unpack('!IIQQII',blob[pos:pos+32]);end=pos+32+size
            need(magic==0x4d525431 and end<=len(blob) and verb in (1,2,3),'actual MRT1 frame')
            frames[sid].append(dict(verb=verb,sequence=seq,address=address,key=key,body=blob[pos+32:end],begin=pos,end=end,time_ns=times[end-1]));pos=end
    return frames,feedback,dict(source_packets=len(raw),physical=dict(counts),source_payload_bytes=sum(source.values()),
        payload_deliveries=sum(map(len,expected.values())),peak_reorder_bytes=dict(peak),fast_retransmits=len(fast))


def audit(r,w,c):
    frames,feedback,report=carrier(r,w,c);t=r['transactions'];transport=t['transport']
    need(not t['gpu_visibility_modeled'] and not t['hardware_calibrated'] and not t['roce_wire_equivalent'],'scope')
    ops={o['id']:o for o in w['operations']};remote={o['id']:o for o in t['operations']};local={o['id']:o for o in r['local_work']}
    need(set(remote)=={i for i,o in ops.items() if o['kind']=='remote'} and set(local)==set(ops)-set(remote),'operation coverage')
    connections={x['id']:x for x in w['connections']};request={};replies={};order={}
    for cid,cx in connections.items():
        rows=sorted((x for x in remote.values() if x['connection']==cid),key=lambda x:x['sequence'])
        need([x['sequence'] for x in rows]==list(range(len(rows))) and len(frames[2*cid])==len(rows),'connection sequence coverage')
        for f,row in zip(frames[2*cid],rows):
            op=ops[row['id']];verb=1 if op['verb']=='put' else 2
            need(f['sequence']==row['sequence']==row['ticket'] and f['verb']==verb and f['key']==op['key'] and f['address']==op['address'],'request intent')
            need(row['applied_ns']==f['time_ns'] and 0<=row['ready_ns']<=row['published_ns']<=f['time_ns'],'actual apply time')
            ack=min(n for n,p in feedback[2*cid] if p>=f['end']);need(row['request_acked_ns']==ack,'actual request ACK')
            request[row['id']]=f;order[cid,f['sequence']]=row['id']
        atomics=[x for x in rows if ops[x['id']]['verb']=='fetch_add']
        need(len(frames[2*cid+1])==len(atomics),'reply count')
        for f,row in zip(frames[2*cid+1],atomics):
            need(f['verb']==3 and f['sequence']==row['sequence'] and f['address']==f['key']==0 and len(f['body'])==8 and
                row['response_ns']==f['time_ns'],'actual atomic reply')
            replies[row['id']]=f
        previous=0
        for row in rows:
            end=max(previous,row['request_acked_ns'],row['response_ns'])
            need(row['completed_ns']==end,'FIFO credit release waits for ACK and atomic reply');previous=end
    # A correctly delivered frame must also have been published before ANY of
    # its bytes were physically sent, including a header split across packets.
    for p in transport['source_packets']:
        packet=dict(zip(transport['source_packet_columns'],p));sid=packet['stream'];cid=sid//2
        for f in frames[sid]:
            if packet['sequence']<f['end'] and f['begin']<packet['sequence']+packet['payload_bytes']:
                i=order[cid,f['sequence']]
                ready=remote[i]['applied_ns' if sid%2 else 'published_ns']
                need(packet['time_ns']>=ready,'source TX precedes request publication/reply production')
    for i,op in ops.items():
        row=remote.get(i,local.get(i))
        for d in op['after']:
            p=remote.get(d['operation'],local.get(d['operation']))
            need(row['ready_ns']>=p[d['event']+'_ns'],'real dependency readiness')
        if i in local:need(row['ready_ns']<=row['start_ns'] and row['completed_ns']==row['start_ns']+op['duration_ns'],'local service duration')
    memory={}
    for win in w['windows']:
        b=bytearray.fromhex(win['hex']) if 'hex' in win else bytearray(win['bytes'])
        for seg in win.get('segments',[]):d=bytes.fromhex(seg['hex']);b[seg['offset']:seg['offset']+len(d)]=d
        memory[win['owner'],win['key']]=win['address'],b
    def span(owner,key,address,n):
        base,b=memory[owner,key];pos=address-base;need(0<=pos and pos+n<=len(b),'memory bounds');return b,pos
    reads={};writes={};snapshots={};sources={};lifecycle=defaultdict(list)
    def overlap(a,b):return a[:2]==b[:2] and a[2]<b[2]+b[3] and b[2]<a[2]+a[3]
    def write(owner,key,address,data,except_copy=None):
        dst=(owner,key,address,len(data))
        need(not any(overlap(dst,x) for x in reads.values()),'write while source leased')
        need(not any(overlap(dst,x) for i,x in writes.items() if i!=except_copy),'write while local target leased')
        b,pos=span(*dst);b[pos:pos+len(data)]=data
    all_events=[]
    for name in ('source_events','target_events','local_events','local_memory_events'):
        all_events.extend((e['order'],name,e) for e in t[name])
    all_events.sort();need([x[0] for x in all_events]==list(range(len(all_events))),'common memory order')
    need([x[2]['time_ns'] for x in all_events]==sorted(x[2]['time_ns'] for x in all_events),'memory time order')
    target_seen=set();last_time=0
    need({e['operation'] for e in t['local_events']}=={i for i,o in ops.items() if 'local_wait' in o},'wait event coverage')
    for _,name,e in all_events:
        last_time=e['time_ns']
        if name=='target_events':
            cid=e['connection'];i=order[cid,e['sequence']];op=ops[i];f=request[i];cx=connections[cid]
            need(i not in target_seen and not e['duplicate'] and e['time_ns']==f['time_ns'] and
                 e['verb']==f['verb'] and e['key']==f['key'] and e['address']==f['address'] and e['body_hex']==f['body'].hex(),'exactly once decoded target')
            target_seen.add(i);body=f['body'];owner=cx['dst']
            if op['verb']=='fetch_add':
                need(body==struct.pack('!Q',op['value']),'wire atomic delta');b,pos=span(owner,op['key'],op['address'],8)
                old=int.from_bytes(b[pos:pos+8],'little')
                need(e['old_value']==remote[i]['returned_value']==int.from_bytes(replies[i]['body'],'big')==old,'actual old value and reply bytes')
                body=((old+op['value'])%(2**64)).to_bytes(8,'little')
            else:
                need(e['old_value']==0,'put old value')
                expected=sources[i] if 'source' in op else bytes.fromhex(op['hex'])
                need(body==expected,'published source/inline payload')
            write(owner,op['key'],op['address'],body)
        elif name=='source_events':
            i=e['operation'];op=ops[i];cx=connections[op['connection']];src=op['source'];key=('source',i)
            rng=(cx['src'],src['key'],src['address'],src['bytes']);kind=e['kind'];lifecycle[key].append(kind)
            need(tuple(e[k] for k in ('owner','key','address','bytes'))==rng,'source lease range')
            need(e['time_ns']==remote[i]['ready_ns' if kind=='acquired' else 'published_ns' if kind=='captured' else 'completed_ns'],'source lease time')
            if kind=='acquired':
                need(key not in reads and not any(overlap(rng,x) for x in writes.values()),'source acquire conflict');reads[key]=rng
            elif kind=='captured':
                need(key in reads,'source capture before acquire');b,pos=span(*rng);sources[i]=bytes(b[pos:pos+rng[3]])
                need(e['hex']==sources[i].hex(),'source capture bytes')
            elif kind=='released':need(key in reads,'source release before acquire');del reads[key]
            else:raise AssertionError('unknown source event')
        elif name=='local_events':
            i=e['operation'];op=ops[i];need(e['time_ns']==local[i]['ready_ns'] and e['owner']==op['rank'] and len(e['conditions'])==len(op['local_wait']),'wait timing')
            for actual,want in zip(e['conditions'],op['local_wait']):
                b,pos=span(op['rank'],want['key'],want['address'],8);old=int.from_bytes(b[pos:pos+8],'little')
                need(all(actual[k]==v for k,v in want.items()) and actual['matched'] and actual['observed_value']==old and old==want['value'],'actual LE64 condition')
                if 'reset_to' in want:write(op['rank'],want['key'],want['address'],want['reset_to'].to_bytes(8,'little'))
        else:
            i=e['operation'];op=ops[i];cp=op['copy'];src=cp['source'];dst=cp['target'];kind=e['kind'];key=('copy',i);lifecycle[key].append(kind)
            a=(op['rank'],src['key'],src['address'],src['bytes']);b=(op['rank'],dst['key'],dst['address'],src['bytes'])
            need(e['source']==dict(zip(('owner','key','address','bytes'),a)) and e['target']==dict(zip(('owner','key','address','bytes'),b)),'copy ranges')
            need(e['time_ns']==local[i]['ready_ns' if kind=='acquired' else 'start_ns' if kind=='captured' else 'completed_ns'],'copy timing')
            if kind=='acquired':
                need(key not in reads and i not in writes and not any(overlap(b,x) for x in reads.values()) and
                     not any(overlap(a,x) or overlap(b,x) for x in writes.values()),'copy acquire conflict')
                reads[key]=a;writes[i]=b
            elif kind=='captured':
                need(key in reads and i in writes,'copy capture before acquire');data,pos=span(*a);snapshots[i]=bytes(data[pos:pos+a[3]])
                need(e['hex']==snapshots[i].hex(),'copy snapshot')
            elif kind=='committed':
                need(e['hex']==snapshots[i].hex() and i in writes,'copy commit bytes');write(*b[:3],snapshots[i],except_copy=i)
            elif kind=='released':need(key in reads and i in writes,'copy release');del reads[key];del writes[i]
            else:raise AssertionError('unknown copy event')
    need(target_seen==set(remote),'target count')
    for i,op in ops.items():
        if 'source' in op:need(lifecycle['source',i]==['acquired','captured','released'],'source lifecycle coverage')
        if 'copy' in op:need(lifecycle['copy',i]==['acquired','captured','committed','released'],'copy lifecycle coverage')
    need(not reads and not writes and not t['active_source_leases'] and not t['active_local_memory_leases'] and not r['pending_operations'] and not r['pending_local_work'],'lease/drain')
    need(len(t['windows'])==len(memory),'window coverage')
    for win in t['windows']:
        base,b=memory[win['owner'],win['key']];actual=bytearray(win['bytes'])
        for page in win['pages']:d=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(d)]=d
        need(win['address']==base and actual==b,'final sparse pages')
    for cx in t['connections']:
        count=sum(o['connection']==cx['id'] for o in remote.values());need(cx['closed'] and cx['reserved']==cx['published']==cx['completed']==count,'transaction queue retirement')
    if 'submission_model' in w:
        pubs=t['submission']['publications'];need(len(pubs)==1 and pubs[0]['exclusive']==2 and pubs[0]['time_ns']==20000,'explicit prefix publication')
        need(remote[0]['prepared_ns']==0 and remote[0]['published_ns']==20000 and all(p[0]>=20000 for p in transport['source_packets']),'prepared is not sent')
    timeouts=[x for x in transport['recovery']['events'] if x[1]==transport['recovery']['event_codes']['timeout_retransmit']]
    report.update(passed=True,remote_operations=len(remote),local_copies=sum('copy' in o for o in ops.values()),
        memory_events=len(all_events),timeouts=len(timeouts),last_memory_event_ns=last_time,
        actual_received_bytes_drive_exactly_once_target=True)
    return report


def online_check(stage,w,r):
    p=stage/'network/online';need(load(p/'initial.json')['operations']==[],'online initial empty')
    response=None;submitted={};marker=None
    for line in (p/'control.jsonl').read_text().splitlines():
        x=json.loads(line);m=x['message']
        if x['direction']=='native':
            for e in m.get('events',[]):
                if e['operation']==1 and e['event']=='response':
                    need(response is None and e['returned_value']==17,'unique actual callback');response=m['now_ns']
        else:
            for op in m['submissions']:
                need(op['id'] not in submitted,'online resubmission');submitted[op['id']]=op
                if op['id']==7:
                    need(response is not None and m['now_ns']>=response and op['hex']==(117).to_bytes(8,'little').hex(),'online value construction');marker=m['now_ns']
    need(submitted=={x['id']:x for x in w['operations']} and marker==response==next(x for x in r['transactions']['operations'] if x['id']==1)['response_ns'],'online callback causality')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    cases={};results={}
    for name,work,proto in [('request-gap','atomic','request'),('reply-tail','atomic','reply-tail'),('chain','chain','middle'),('explicit','explicit',''),('online','online','middle')]:
        r=load(a.stage/'network'/name/'result.json');w=load(a.stage/'inputs'/(work+'.json'));c=load(a.stage/'inputs'/((proto+'-' if proto else '')+'protocol.json'))
        cases[name]=audit(r,w,c);results[name]=(r,w,c)
        if name=='online':online_check(a.stage,w,r)
    need(cases['reply-tail']['timeouts']>=1 and cases['request-gap']['peak_reorder_bytes'].get(0,0)>0,'loss coverage')
    rejected=[];r,w,c=results['chain']
    for kind in ('delivery_bytes','delivery_uid','target_value','final_page','ack_time','source_release','copy_snapshot'):
        x=deepcopy(r);t=x['transactions']
        if kind.startswith('delivery_'):
            e=next(e for e in t['transport']['selective_repeat']['events'] if e['kind']=='payload_delivered')
            if kind=='delivery_bytes':e['hex']='ff'+e['hex'][2:]
            else:e['packet_uid']+=99999
        elif kind=='target_value':next(e for e in t['target_events'] if e['verb']==2)['old_value']+=1
        elif kind=='final_page':t['windows'][0]['pages'][0]['hex']='ff'+t['windows'][0]['pages'][0]['hex'][2:]
        elif kind=='ack_time':t['operations'][0]['request_acked_ns']-=1
        elif kind=='source_release':next(e for e in t['source_events'] if e['kind']=='released')['time_ns']-=1
        else:next(e for e in t['local_memory_events'] if e['kind']=='captured')['hex']='ff'
        try:audit(x,w,c)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('tamper accepted: '+kind)
    save(a.output,dict(passed=True,cases=cases,tampering_rejected=rejected,
        scope='finite functional fixtures; independent actual packet, ordered bytes, MRT1 and memory replay; no application performance claim'))
    print(json.dumps(dict(passed=True,cases=len(cases),tamper_classes=len(rejected))))


if __name__=='__main__':main()
