"""Independent standard-library audit for the chained application-windows example.

Only lossless single-frame PUT with one preceding local copy is supported.
The packet capture supplies data and endpoint NIC; metadata never supplies bytes.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import struct


def need(ok, why):
    if not ok: raise AssertionError(why)


def audit(result, work):
    t=result['transactions'];a=t['application_windows'];transport=t['transport']
    need(result['passed'] and result['drained'] and transport['physical_drained'], 'drain')
    need(a['mode']=='local_views/1' and not a['provider_retirement_modeled'], 'model boundary')
    need(t['registrations']==work['registrations'], 'static registration changed')
    ops=work['operations'];need(len(ops)==5 and [o['id'] for o in ops]==list(range(5)), 'fixture shape')
    local={r['id']:r for r in result['local_work']};remote=t['operations']
    need(set(local)=={0,1,2,4} and len(remote)==1 and remote[0]['id']==3, 'operation coverage')
    remote=remote[0];times={i:local[i] for i in local}
    times[3]=dict(ready_ns=remote['ready_ns'],start_ns=remote['published_ns'],completed_ns=remote['completed_ns'])
    for i,op in enumerate(ops):
        r=times[i];need(0<=r['ready_ns']<=r['start_ns']<=r['completed_ns'], 'operation times')
        if op['kind']=='local':need(r['completed_ns']==r['start_ns']+op['duration_ns'], 'local duration')
        if i:need(r['ready_ns']>=times[i-1]['completed_ns'], 'fixture causal chain')
    initial={}
    for w in work['windows']:
        data=bytearray.fromhex(w['hex']) if 'hex' in w else bytearray(w['bytes'])
        for seg in w.get('segments',[]):
            b=bytes.fromhex(seg['hex']);data[seg['offset']:seg['offset']+len(b)]=b
        initial[w['owner'],w['key']]=w['address'],data
    storage=deepcopy(initial)
    def span(owner,key,address,size):
        base,data=storage[owner,key];offset=address-base
        need(0<=offset and offset+size<=len(data), 'storage bounds');return data,offset
    cp=ops[1]['copy'];src=cp['source'];dst=cp['target'];size=src['bytes']
    data,pos=span(4,src['key'],src['address'],size);snapshot=bytes(data[pos:pos+size])
    target,pos=span(4,dst['key'],dst['address'],size);target[pos:pos+size]=snapshot
    copy_events=t['local_memory_events']
    need([e['kind'] for e in copy_events]==['acquired','captured','committed','released'], 'copy events')
    for e in copy_events:
        need(e['operation']==1 and e['time_ns']==times[1]['ready_ns' if e['kind']=='acquired' else
             'start_ns' if e['kind']=='captured' else 'completed_ns'], 'copy event time')
        if 'hex' in e:need(e['hex']==snapshot.hex(), 'actual local copy bytes')

    packets=transport['source_packets'];payloads=transport['source_payload_packets']
    need(len(packets)==len(payloads)==1, 'single actual frame')
    packet=dict(zip(transport['source_packet_columns'],packets[0]));payload=payloads[0]
    need(all(packet[k]==payload[k] for k in ('time_ns','stream','sequence')), 'packet payload identity')
    raw=bytes.fromhex(payload['hex']);need(len(raw)==packet['payload_bytes'] and len(raw)>=32,'payload length')
    magic,verb,sequence,address,key,length=struct.unpack('!IIQQII',raw[:32]);body=raw[32:]
    need(magic==0x4d525431 and verb==1 and sequence==0 and len(body)==length,'MRT1 request')
    op=ops[3];connection=next(c for c in work['connections'] if c['id']==op['connection'])
    need(key==op['key'] and address==op['address'] and packet['stream']==2*connection['id'], 'wire request')
    source=op['source'];data,pos=span(connection['src'],source['key'],source['address'],source['bytes'])
    need(body==data[pos:pos+source['bytes']], 'wire source contents')
    capture=result['packet_capture'];need(capture['complete'] and not capture['selection']['nodes'],'capture coverage')
    wire=capture['wire_headers'];tx=[];rx=[];acks=[]
    need(len(wire['event_header_indices'])==len(capture['events']),'wire header coverage')
    for i,row in enumerate(capture['events']):
        event=dict(zip(capture['columns'],row));header=dict(zip(wire['columns'],wire['headers'][wire['event_header_indices'][i]]))
        if event['packet_uid']==packet['packet_uid'] and event['ip_protocol']==17:
            if event['event']==0 and event['node']==connection['src']:tx.append(event)
            if event['event']==1 and event['node']==connection['dst']:rx.append(event)
            need(header['source_port']==header['destination_port']==connection['request_port'],'packet port')
        if event['event']==1 and event['node']==connection['src'] and event['ip_protocol']==252 and header['ports_present'] and header['destination_port']==connection['request_port']:
            acks.append(event)
    need(len(tx)==len(rx)==1 and tx[0]['port']==connection['source_nic'] and rx[0]['port']==connection['destination_nic'],'actual endpoint NIC')
    need(remote['published_ns']<=tx[0]['time_ns']==packet['time_ns'] and remote['applied_ns']==rx[0]['time_ns'],'actual TX/RX')
    need(times[2]['completed_ns']<=tx[0]['time_ns']<=rx[0]['time_ns'],'last target view closes before TX/RX')
    need(acks and remote['request_acked_ns']==min(e['time_ns'] for e in acks if e['sequence']>=len(raw)) and
         remote['completed_ns']==remote['request_acked_ns'],'actual ACK completion')
    reg=next(r for r in work['registrations'] if (r['owner'],r['nic'],r['key'])==(connection['dst'],rx[0]['port'],key))
    need(reg['address']<=address and address+length<=reg['address']+reg['bytes'],'wire authority range')
    data,pos=span(connection['dst'],reg['window_key'],address,length);data[pos:pos+length]=body
    target_events=t['target_events'];need(len(target_events)==1,'target event count')
    e=target_events[0]
    need(all(e[k]==v for k,v in dict(time_ns=rx[0]['time_ns'],connection=connection['id'],sequence=0,verb=1,
         key=key,address=address,body_hex=body.hex(),destination_nic=rx[0]['port'],duplicate=False,old_value=0).items()),'target event matches RX')
    need([e['hex'] for e in t['source_events'] if e['kind']=='captured']==[body.hex()],'captured source')
    need(not t['active_source_leases'] and not t['active_local_memory_leases'],'active byte leases')

    # Replay view identity/refcounts from INPUT and actual completion times.
    views={v['id']:v for v in work['application_windows']};open_=set(views);expected=[]
    for op in ops:
        i=op['id'];ids=op.get('application_uses',[])
        if ids:
            need(all(v in open_ for v in ids),'use of closed view')
            expected.extend([dict(operation=i,kind='acquire',views=ids,time_ns=times[i]['ready_ns']),
                             dict(operation=i,kind='release',views=ids,time_ns=times[i]['completed_ns'])])
        if 'release_window' in op:
            v=op['release_window'];need(v in open_,'duplicate close');open_.remove(v);view=views[v]
            refs=sum(views[x]['owner']==view['owner'] and views[x]['window_key']==view['window_key'] for x in open_)
            expected.append(dict(operation=i,kind='close',view=v,owner=view['owner'],window_key=view['window_key'],
                                 remaining_application_refs=refs,time_ns=times[i]['completed_ns']))
    need([{k:e[k] for k in want} for e,want in zip(a['events'],expected)]==expected and len(a['events'])==len(expected),'application lifecycle')
    orders=[e['order'] for e in a['events']];need(orders==sorted(set(orders)), 'lifecycle order')
    need(len(a['views'])==len(views) and {v['id'] for v in a['views']}==set(views),'view coverage')
    for v in a['views']:
        need(v['closed'] and not v['active_operations'] and all(v[k]==views[v['id']][k] for k in views[v['id']]),'final application view')
    expected_domains={(v['owner'],v['window_key']) for v in views.values()}
    need(len(a['backing_domains'])==len(expected_domains) and {(d['owner'],d['window_key']) for d in a['backing_domains']}==expected_domains,'backing domain coverage')
    for d in a['backing_domains']:
        aliases=[r['id'] for r in work['registrations'] if (r['owner'],r['window_key'])==(d['owner'],d['window_key'])]
        need(d['application_refs']==0 and d['registration_ids']==aliases and d['state']=='no_application_refs_provider_pending','pending is not retirement')
    need(len(t['windows'])==len(storage),'physical storage count')
    for w in t['windows']:
        base,expected_bytes=storage[w['owner'],w['key']]
        actual=bytearray.fromhex(w['hex']) if 'hex' in w else bytearray(w['bytes'])
        for page in w.get('pages',[]):
            b=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(b)]=b
        need(w['address']==base and actual==expected_bytes,'final actual bytes')
    return dict(schema_version='application-windows-independent-audit/1',views=len(views),backing_domains=len(expected_domains),
                actual_source_packets=1,wire_payload_bytes=len(raw),lifecycle_events=len(expected),
                target_last_view_closed_ns=times[2]['completed_ns'],actual_target_rx_ns=rx[0]['time_ns'],
                actual_source_completed_ns=remote['completed_ns'],static_authority_survives_local_view_release=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('result','work','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();raw=json.loads(args.result.read_text());work=json.loads(args.work.read_text())
    report=audit(raw,work);rejected=[]
    for kind in ('close_time','reference_count','wire_body','final_memory','provider_claim'):
        v=deepcopy(raw);a=v['transactions']['application_windows']
        if kind=='close_time':a['events'][0]['time_ns']+=1
        elif kind=='reference_count':a['events'][0]['remaining_application_refs']=0
        elif kind=='wire_body':
            x=v['transactions']['transport']['source_payload_packets'][0];x['hex']=x['hex'][:-2]+'ff'
        elif kind=='final_memory':
            x=v['transactions']['windows'][-1];x=x if 'hex' in x else x['pages'][0];x['hex']='ff'+x['hex'][2:]
        else:a['provider_retirement_modeled']=True
        try:audit(v,work)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('tamper accepted: '+kind)
    report['tampering_rejected']=rejected
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
