"""Complete source/frame/work ledger, explicitly partial physical-wire proof."""
from collections import Counter,defaultdict
import struct


def transport_audit(result,initial,nodes):
    tx=result['transactions'];net=tx['transport'];conns={c['id']:c for c in initial['connections']}
    remote={i:c for i,c in conns.items() if c['src']!=c['dst']}
    assert {s['id'] for s in net['streams']}=={2*i+j for i in remote for j in (0,1)}
    raw=defaultdict(dict);packets={};duplicates=0;starts=set()
    assert len(net['source_packets'])==len(net['source_payload_packets'])
    for row,payload in zip(net['source_packets'],net['source_payload_packets']):
        time,sid,src,dst,nic,seq,size,uid=row;c=remote[sid//2]
        assert (src,dst,nic)==((c['dst'],c['src'],c['destination_nic']) if sid%2 else (c['src'],c['dst'],c['source_nic']))
        body=bytes.fromhex(payload['hex']);assert (time,sid,seq,size)==(payload['time_ns'],payload['stream'],payload['sequence'],len(body))
        duplicates+=(sid,seq) in starts;starts.add((sid,seq));packets[uid]=row
        for i,b in enumerate(body):assert raw[sid].get(seq+i,b)==b;raw[sid][seq+i]=b
    frames={};work={(w['stream'],w['begin'],w['end']):w for w in net['work']}
    assert len(work)==len(net['work'])
    for stream in net['streams']:
        sid=stream['id'];n=stream['submitted_bytes'];c=remote[sid//2]
        assert stream['qp_creations']==1 and stream['snd_una']==stream['snd_nxt']==stream['published_bytes']==n
        assert set(raw[sid])==set(range(n))
        data=bytes(raw[sid][i] for i in range(n));offset=0;count=0
        while offset<n:
            magic,verb,sequence,address,key,size=struct.unpack_from('>IIQQII',data,offset)
            assert magic==0x4d525431 and verb in (1,2,3)
            end=offset+32+size;assert end<=n
            w=work[sid,offset,end];assert w['published_ns']<=w['accepted_ns']<=w['acked_ns']<=result['finish_ns']
            assert (sid,sequence) not in frames
            frames[sid,sequence]=dict(verb=verb,address=address,key=key,body=data[offset+32:end],work=w)
            offset=end;count+=1
        assert stream['accepted_work']==stream['ack_completed_work']==count
    for op in tx['operations']:
        node=nodes[op['id']];c=conns[op['connection']]
        if c['src']==c['dst']:
            assert op['delivery']=='declared_self_service/1' and op['request_acked_ns']==op['response_ns']==-1
            continue
        frame=frames[2*op['connection'],op['sequence']]
        body=node['value'].to_bytes(8,'big') if node['verb']=='fetch_add' else next(bytes.fromhex(e['hex']) for e in tx['source_events']
            if e['operation']==op['id'] and e['kind']=='captured') if 'source' in node else bytes.fromhex(node['hex'])
        assert (frame['verb'],frame['key'],frame['address'],frame['body'])==(2 if node['verb']=='fetch_add' else 1,node['key'],node['address'],body)
        w=frame['work'];assert (w['published_ns'],w['accepted_ns'],w['acked_ns'])==(op['published_ns'],op['applied_ns'],op['request_acked_ns'])
        if node['verb']=='fetch_add':
            reply=frames[2*op['connection']+1,op['sequence']]
            assert reply['verb']==3 and int.from_bytes(reply['body'],'big')==op['returned_value']
            assert reply['work']['accepted_ns']==op['response_ns']
    for cid,c in conns.items():
        if cid in remote:continue
        last=0
        for op in sorted((o for o in tx['operations'] if o['connection']==cid),key=lambda o:o['sequence']):
            begin=max(last,op['published_ns']);assert op['self_started_ns']==begin
            assert op['applied_ns']==begin+c['self_service']['apply_ns']
            assert op['completed_ns']==op['self_completed_ns']==begin+c['self_service']['complete_ns'];last=op['completed_ns']
    capture=result['packet_capture'];headers=capture['wire_headers'];indices=headers['event_header_indices']
    assert len(indices)==len(capture['events'])
    endpoint_data=endpoint_ack=0;previous=-1
    for values,index in zip(capture['events'],indices):
        e=dict(zip(capture['columns'],values));h=dict(zip(headers['columns'],headers['headers'][index]))
        assert e['packet_uid']==h['packet_uid'] and e['time_ns']>=previous;previous=e['time_ns']
        if e['ip_protocol']==17:
            assert e['packet_uid'] in packets
            p=packets[e['packet_uid']];c=remote[p[1]//2]
            assert (e['sequence'],e['data_payload_bytes'])==(p[5],p[6])
            assert h['source_port']==h['destination_port']==(c['response_port'] if p[1]%2 else c['request_port'])
            if e['node'] in (c['src'],c['dst']):endpoint_data+=1
        elif e['ip_protocol']==252:endpoint_ack+=int(e['node'] in {c['src'] for c in conns.values()})
    assert net['physical_drained'] and not net['inflight_packets'] and net['closed']
    return dict(actual_source_packets=len(net['source_packets']),retransmitted_source_packets=duplicates,
        decoded_frames=len(frames),complete_work_records=len(work),physical_capture_events=len(capture['events']),
        checked_captured_endpoint_data_records=endpoint_data,observed_endpoint_ack_records=endpoint_ack,
        full_physical_path_or_ack_audit_complete=capture['complete'],
        omitted_physical_records=capture['omitted_events'],
        transport_scope='complete source bytes/frames/work/transaction memory; physical data and ACK validation restricted to capture prefix')
