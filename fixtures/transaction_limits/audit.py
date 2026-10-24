"""Independent MRT1 source bytes, endpoint feedback, target state and queue audit.

Supports only the versioned expanded and long-sequence functional inputs.
No platform decoder, operation-replay helper or simulator fixture is imported.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import struct


def need(ok, why):
    if not ok:raise AssertionError(why)


def audit(r,w):
    t=r['transactions'];transport=t['transport'];limits=w['execution_limits']
    need(r['passed'] and r['drained'] and transport['physical_drained'] and transport['closed'],'completion/drain')
    need(t['execution_limits']==limits and not t['gpu_visibility_modeled'],'effective limits/model scope')
    connections={c['id']:c for c in w['connections']}
    initial={};memory={}
    for win in w['windows']:
        b=bytearray(win['bytes'])
        for seg in win['segments']:
            data=bytes.fromhex(seg['hex']);b[seg['offset']:seg['offset']+len(data)]=data
        initial[win['owner'],win['key']]=(win['address'],bytes(b))
        memory[win['owner'],win['key']]=(win['address'],bytearray(b))
    expected=defaultdict(bytes);seq=Counter();by_frame={};remote={};local={}
    returned={};counts=Counter()
    for op in w['operations']:
        if op['kind']=='local':local[op['id']]=op;continue
        c=connections[op['connection']];s=seq[c['id']];seq[c['id']]+=1;remote[op['id']]=op
        if op['verb']=='put':
            source=op['source'];base,data=initial[c['src'],source['key']]
            pos=source['address']-base;body=data[pos:pos+source['bytes']];verb=1
        else:
            body=struct.pack('!Q',op['value']);verb=2
            # The only atomic input is a single ordered connection to one word.
            need(len(connections)==1,'atomic oracle scope')
            old=sum(o['value'] for o in list(remote.values())[:-1])%(2**64)
            returned[op['id']]=old
            reply=struct.pack('!IIQQIIQ',0x4d525431,3,s,0,0,8,old)
            expected[2*c['id']+1]+=reply
        frame=struct.pack('!IIQQII',0x4d525431,verb,s,op['address'],op['key'],len(body))+body
        begin=len(expected[2*c['id']]);expected[2*c['id']]+=frame
        by_frame[c['id'],s]=(op,body,begin,begin+len(frame));counts[c['id']]+=1
    need(len(t['operations'])==len(remote) and len(r['local_work'])==len(local),'all operation coverage')
    operations={o['id']:o for o in t['operations']};locals_={o['id']:o for o in r['local_work']}
    packets=[dict(zip(transport['source_packet_columns'],p)) for p in transport['source_packets']]
    payloads=transport['source_payload_packets'];need(len(packets)==len(payloads),'source payload coverage')
    capture=r['packet_capture'];need(capture['complete'] and not capture['selection']['nodes'],'complete physical capture')
    wire=capture['wire_headers'];need(len(wire['event_header_indices'])==len(capture['events']),'actual header coverage')
    physical=[];by_uid=defaultdict(list)
    for i,row in enumerate(capture['events']):
        e=dict(zip(capture['columns'],row));h=dict(zip(wire['columns'],wire['headers'][wire['event_header_indices'][i]]))
        e.update(header=h);physical.append(e);by_uid[e['packet_uid']].append(e)
    receive_candidates=defaultdict(list);ack_candidates=defaultdict(list);unique_bytes=defaultdict(set)
    for packet,payload in zip(packets,payloads):
        stream=packet['stream'];raw=bytes.fromhex(payload['hex']);pos=packet['sequence'];end=pos+len(raw)
        need(all(payload[k]==packet[k] for k in ('time_ns','stream','sequence')),'source row identity')
        need(len(raw)==packet['payload_bytes'] and raw==expected[stream][pos:end],'independent actual MRT1 wire bytes')
        unique_bytes[stream].update(range(pos,end))
        c=connections[stream//2];reverse=stream%2
        src,dst=(c['dst'],c['src']) if reverse else (c['src'],c['dst'])
        sn,dn=(c['destination_nic'],c['source_nic']) if reverse else (c['source_nic'],c['destination_nic'])
        port=c['response_port'] if reverse else c['request_port']
        events=by_uid[packet['packet_uid']]
        tx=[e for e in events if e['event']==0 and e['node']==src and e['ip_protocol']==17]
        need(len(tx)==1 and tx[0]['port']==sn and tx[0]['time_ns']==packet['time_ns'],'actual source NIC/TX')
        for e in events:
            if e['ip_protocol']==17:
                need(e['header']['source_port']==port and e['header']['destination_port']==port,'wire ports')
        rx=[e for e in events if e['event']==1 and e['node']==dst and e['ip_protocol']==17]
        need(len(rx)<=1,'duplicate endpoint capture')
        for e in rx:
            need(e['port']==dn and e['time_ns']>=packet['time_ns'],'actual target NIC/RX')
            receive_candidates[stream].append((e['time_ns'],pos,end))
    for stream,data in expected.items():need(unique_bytes[stream]==set(range(len(data))),'every byte actually transmitted')
    for e in physical:
        if e['event']!=1 or e['ip_protocol']!=252 or not e['header']['ports_present']:continue
        for c in connections.values():
            if e['node']==c['src'] and e['header']['destination_port']==c['request_port']:
                ack_candidates[2*c['id']].append((e['time_ns'],e['sequence']))
            if e['node']==c['dst'] and e['header']['destination_port']==c['response_port']:
                ack_candidates[2*c['id']+1].append((e['time_ns'],e['sequence']))
    def prefix_time(stream,end):
        prefix=0
        for time,begin,stop in sorted(receive_candidates[stream]):
            if begin<=prefix:prefix=max(prefix,stop)
            if prefix>=end:return time
        raise AssertionError('no physical contiguous receive prefix')
    target_events=t['target_events'];need(len(target_events)==len(remote),'exactly-once target request count')
    seen=set()
    for e in target_events:
        identity=e['connection'],e['sequence'];need(identity in by_frame and identity not in seen,'target request identity')
        seen.add(identity);op,body,begin,end=by_frame[identity];c=connections[identity[0]];o=operations[op['id']]
        need(not e['duplicate'] and e['body_hex']==body.hex() and e['key']==op['key'] and e['address']==op['address'],'target actual frame')
        need(e['verb']==(1 if op['verb']=='put' else 2),'target verb')
        if 'destination_nic' in e:need(e['destination_nic']==c['destination_nic'],'target scoped authority')
        need(e['time_ns']==prefix_time(2*c['id'],end)==o['applied_ns'],'actual receive causes apply')
        need(o['sequence']==identity[1] and o['connection']==identity[0] and 0<=o['ready_ns']<=o['published_ns']<=o['applied_ns'],'operation admission/publish')
        ack=min(time for time,seq in ack_candidates[2*c['id']] if seq>=end)
        need(o['request_acked_ns']==ack,'actual cumulative ACK')
        base,data=memory[c['dst'],op['key']];offset=op['address']-base
        if op['verb']=='put':
            data[offset:offset+len(body)]=body;need(o['completed_ns']==ack,'put credit release')
        else:
            old=int.from_bytes(data[offset:offset+8],'little');need(old==returned[op['id']]==e['old_value']==o['returned_value'],'atomic old value')
            data[offset:offset+8]=((old+op['value'])%(2**64)).to_bytes(8,'little')
            reply_end=(identity[1]+1)*40
            need(o['response_ns']==prefix_time(2*c['id']+1,reply_end),'actual reply arrival')
            need(o['completed_ns']==max(ack,o['response_ns']),'atomic release waits for response and ACK')
    copy_events=t['local_memory_events']
    for identity,op in local.items():
        row=locals_[identity];source=op['copy']['source'];target=op['copy']['target'];rank=op['rank']
        base,data=memory[rank,source['key']];offset=source['address']-base;snapshot=bytes(data[offset:offset+source['bytes']])
        base,out=memory[rank,target['key']];offset=target['address']-base;out[offset:offset+len(snapshot)]=snapshot
        need(0<=row['ready_ns']<=row['start_ns'] and row['completed_ns']==row['start_ns']+op['duration_ns'],'local declared duration')
        events=[e for e in copy_events if e['operation']==identity]
        need([e['kind'] for e in events]==['acquired','captured','committed','released'],'copy lifecycle')
        for e in events:
            need(e['time_ns']==row['ready_ns' if e['kind']=='acquired' else 'start_ns' if e['kind']=='captured' else 'completed_ns'],'copy event time')
            if 'hex' in e:need(e['hex']==snapshot.hex(),'copy actual bytes')
    for op in w['operations']:
        row=locals_[op['id']] if op['kind']=='local' else operations[op['id']]
        for d in op['after']:
            parent=operations[d['operation']]
            need(row['ready_ns']>=parent[d['event']+'_ns'],'real dependency readiness')
    for win in t['windows']:
        base,expected_data=memory[win['owner'],win['key']];actual=bytearray(win['bytes'])
        need(base==win['address'] and len(expected_data)==win['bytes'],'window logical extent')
        for page in win['pages']:
            data=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(data)]=data
        need(actual==expected_data,'independently reconstructed final memory')
    need(set(seen)==set(by_frame),'all target sequences')
    need(len(t['connections'])==len(connections) and len(transport['streams'])==2*len(connections),'expanded context coverage')
    for c in t['connections']:
        need(c['closed'] and c['reserved']==c['published']==c['completed']==counts[c['id']],'queue exact retirement')
    need(not t['active_source_leases'] and not t['active_local_memory_leases'] and not r['pending_operations'] and not r['pending_local_work'],'resource lifetime')
    for s in transport['streams']:
        need(s['qp_creations']==1 and s['submitted_bytes']==s['published_bytes']==s['snd_una']==len(expected[s['id']]),'persistent stream prefix')
    return dict(passed=True,connections=len(connections),remote_operations=len(remote),local_copies=len(local),
        source_packets=len(packets),unique_request_bytes=sum(len(v) for k,v in expected.items() if k%2==0),
        unique_reply_bytes=sum(len(v) for k,v in expected.items() if k%2),
        physically_transmitted_payload_bytes=sum(p['payload_bytes'] for p in packets),
        max_request_sequence=max(s for _,s in by_frame),actual_receive_ack_and_response_checked=True)


def main():
    p=argparse.ArgumentParser()
    for name in ('result','work','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=json.loads(a.result.read_text());w=json.loads(a.work.read_text())
    summary=audit(r,w);a.output.write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
