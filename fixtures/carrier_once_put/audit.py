"""Independent single-frame PUT delivery, retained state, pool and window audit."""
import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import struct


def need(ok, reason):
    if not ok: raise AssertionError(reason)


def audit(result, work):
    t=result['transactions'];transport=t['transport'];pool=transport['payload_pool']
    enabled=work['buffer_model'].get('on_source_isolation')=='release_copied_payload/1'
    once=work['buffer_model'].get('target_delivery')=='carrier_once_put/1'
    need(pool.get('on_source_isolation')==('release_copied_payload/1' if enabled else None),'release policy')
    need(pool['capacity_bytes_per_source']==work['buffer_model']['transport_bytes_per_source'],'capacity')
    streams={s['id']:s for s in transport['streams']};works={w['id']:w for w in transport['work']}
    recovery=transport['recovery'];need(recovery['complete'],'truncated recovery')
    failures={r['stream']:r['time_ns'] for r in recovery.get('failures',[])}
    observed_failure={r['message_id']:r['time_ns'] for values in recovery['events']
        if (r:=dict(zip(recovery['columns'],values)))['event']==4}
    need(observed_failure==failures,'failure events')
    used=defaultdict(int);peak=defaultdict(int);held={};discarded=[];released=0;last=-1
    for e in pool['events']:
        need(e['time_ns']>=last,'pool time order');last=e['time_ns']
        w=works[e['work']];stream=streams[e['stream']];source=e['source'];key=(e['stream'],e['work'])
        need(w['stream']==e['stream'] and stream['src']==source and w['bytes']==e['bytes'],'pool identity')
        if e['kind']=='acquired':
            need(key not in held and w['ready_ns']==e['time_ns'],'acquire')
            held[key]=e['bytes'];used[source]+=e['bytes'];peak[source]=max(peak[source],used[source])
        else:
            need(held.pop(key,None)==e['bytes'],'unowned release')
            if e['kind']=='released':
                need(w['acked_ns']==e['time_ns']>=0,'release without actual full ACK');released+=1
            else:
                need(e['kind']=='discarded_on_source_isolation' and enabled,'undeclared discard')
                need(failures.get(e['stream'])==e['time_ns'] and w['acked_ns']==-1,'premature discard or forged ACK')
                discarded.append(dict(stream=e['stream'],work=e['work'],bytes=e['bytes'],time_ns=e['time_ns']))
            used[source]-=e['bytes']
        need(0<=used[source]<=pool['capacity_bytes_per_source'] and used[source]==e['used_bytes'],'pool conservation')
    need({(x['stream'],x['work']):x['bytes'] for x in pool['held']}==held,'held payload')
    for owner in pool['owners']:
        need(owner['used_bytes']==used[owner['source']] and owner['peak_bytes']==peak[owner['source']],'owner peak/current')
    need(sorted(pool.get('discarded_work',[]),key=lambda e:(e['stream'],e['work']))==
        sorted(discarded,key=lambda e:(e['stream'],e['work'])),'discard summary')
    capture=result['packet_capture'];need(capture['complete'] and capture['selection']['nodes']==[],'incomplete full capture')
    rows=[dict(zip(transport['source_packet_columns'],v)) for v in transport['source_packets']]
    payloads=transport['source_payload_packets'];need(len(rows)==len(payloads),'payload packet coverage')
    packets={};windows={(w['owner'],w['key']):(w['address'],bytearray.fromhex(w['hex'])) for w in work['windows']}
    ops={op['id']:op for op in t['operations']};inputs={op['id']:op for op in work['operations']}
    op_sequence={(op['connection'],op['sequence']):op for op in t['operations']}
    captures={e['operation']:e for e in t['source_events'] if e['kind']=='captured'}
    for row,payload in zip(rows,payloads):
        raw=bytes.fromhex(payload['hex']);stream=streams[row['stream']]
        need(all(payload[k]==row[k] for k in ('time_ns','stream','sequence')) and len(raw)==row['payload_bytes'],'payload association')
        need(row['packet_uid'] not in packets,'packet UID collision in fixture')
        need(row['stream'] not in failures or row['time_ns']<failures[row['stream']],'source TX after isolation')
        magic,verb,seq,address,key,size=struct.unpack('!IIQQII',raw[:32])
        need(magic==0x4d525431 and verb==1 and len(raw)==32+size,'MRT1 frame')
        operation=op_sequence[row['stream']//2,seq];declared=inputs[operation['id']]
        src=declared['source'];origin,content=windows[stream['src'],src['key']]
        expected=content[src['address']-origin:src['address']-origin+src['bytes']]
        need(raw[32:]==expected and payload['hex'][64:]==captures[operation['id']]['hex'],'source payload changed')
        need(address==declared['address'] and key==declared['key'] and operation['published_ns']<=row['time_ns'],'wire address/publication')
        packets[row['packet_uid']]=(row,raw,(verb,seq,address,key,raw[32:]))
    decoded=[];prefix=defaultdict(int);actual_acks=defaultdict(list);duplicate_rx=0
    wire=capture['wire_headers']
    for index,values in enumerate(capture['events']):
        e=dict(zip(capture['columns'],values))
        header=dict(zip(wire['columns'],wire['headers'][wire['event_header_indices'][index]]))
        if e['event']==1 and e['ip_protocol']==252:
            for sid,stream in streams.items():
                if e['node']==stream['src'] and header['ports_present'] and header['source_port']==header['destination_port']==stream['wire_port']:
                    actual_acks[sid].append((e['time_ns'],e['sequence']))
        if e['event']!=1 or e['ip_protocol']!=17 or e['packet_uid'] not in packets:continue
        row,raw,frame=packets[e['packet_uid']];sid=row['stream'];stream=streams[sid]
        if e['node']!=stream['dst']:continue
        need(header['source_port']==header['destination_port']==stream['wire_port'],'wire identity')
        if row['sequence']<prefix[sid]:duplicate_rx+=1
        if row['sequence']!=prefix[sid]:continue
        prefix[sid]+=len(raw);verb,seq,address,key,body=frame
        base,content=windows[stream['dst'],key];offset=address-base
        need(0<=offset and offset+len(body)<=len(content),'target range')
        content[offset:offset+len(body)]=body
        decoded.append((e['time_ns'],sid//2,seq,verb,key,address,body.hex()))
    recorded=[(e['time_ns'],e['connection'],e['sequence'],e['verb'],e['key'],e['address'],e['body_hex']) for e in t['target_events']]
    need(decoded==recorded and all(not e['duplicate'] for e in t['target_events']),'target effects differ from real contiguous RX')
    for w in t['windows']:need(w['hex']==windows[w['owner'],w['key']][1].hex(),'final target memory')
    for w in works.values():
        if w['acked_ns']>=0:
            need(any(time==w['acked_ns'] and seq>=w['end'] for time,seq in actual_acks[w['stream']]),'ACK completion absent from wire')
        if w['stream'] in failures:need(w['acked_ns']==-1,'failed stream completion forged')
    for op in ops.values():
        if op['connection']*2 in failures:
            need(op['request_acked_ns']==op['completed_ns']==-1,'failed transaction completion forged')
    lease={e['operation'] for e in t['active_source_leases']}
    need(lease=={op['id'] for op in ops.values() if op['completed_ns']<0},'failed source lease released')
    cache_bytes=defaultdict(int);lease_bytes=defaultdict(int)
    for _,connection,_,_,_,_,body in decoded:
        cache_bytes[streams[connection*2]['dst']]+=32+len(bytes.fromhex(body))
    for item in t['active_source_leases']:lease_bytes[item['owner']]+=item['bytes']
    for owner in t['buffer_retention']['owners']:
        need(owner['target_cache_bytes']==(0 if once else cache_bytes[owner['owner']]),'target cache incorrectly freed')
        need(owner['source_lease_referenced_bytes']==lease_bytes[owner['owner']],'lease accounting')
    for identity,op in ops.items():
        if op['published_ns']<0:need(identity not in captures,'unpublished source captured')
    if once:
        need(t['target_delivery']['mode']=='carrier_once_put/1','target policy')
        actual={c['context']:c for c in t['target_delivery']['contexts']}
        for connection in {op['connection'] for op in ops.values()}:
            item=actual[connection]
            need(item['next_sequence']==sum(c==connection for _,c,*_ in decoded),'target sequence prefix')
            need(item['retained_requests']==item['retained_encoded_bytes']==0,'retained request cache')
    else:need('target_delivery' not in t,'default target metadata changed')
    return dict(schema_version='carrier-once-independent-audit/1',duplicate_carrier_receives=duplicate_rx,source_packets=len(rows),target_applications=len(decoded),
        pool_ack_releases=released,isolated_copy_discards=len(discarded),retained_payload_bytes=sum(held.values()),
        retained_source_lease_ids=sorted(lease),late_target_effects=sum(time>failures.get(conn*2,10**18) for time,conn,*_ in decoded),
        pool_and_wire_and_memory_consistent=True,original_run_passed=result['passed'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('result','work','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();raw=json.loads(a.result.read_text());work=json.loads(a.work.read_text());report=audit(raw,work)
    tests=['packet_payload','owner_peak','target_memory','target_cache','application_replay']
    pool=raw['transactions']['transport']['payload_pool']
    if pool.get('discarded_work'):tests+=['early_discard','fake_failed_ack']
    rejected=[]
    for kind in tests:
        value=deepcopy(raw);t=value['transactions'];pool=t['transport']['payload_pool']
        if kind=='application_replay':
            t['target_events'].append(deepcopy(t['target_events'][0]))
        elif kind=='target_memory':
            window=t['windows'][-1];window['hex']='ff'+window['hex'][2:]
        elif kind=='target_cache':
            t['buffer_retention']['owners'][-1]['target_cache_bytes']+=1
        elif kind=='packet_payload':
            packet=t['transport']['source_payload_packets'][0];packet['hex']=packet['hex'][:-2]+'ff'
        elif kind=='owner_peak':pool['owners'][0]['peak_bytes']+=1
        elif kind=='early_discard':next(e for e in pool['events'] if e['kind']=='discarded_on_source_isolation')['time_ns']-=1
        else:t['operations'][0]['request_acked_ns']=0
        try:audit(value,work)
        except AssertionError:rejected.append(kind)
        else:raise AssertionError('tampering accepted: '+kind)
    report['tampering_rejected']=rejected
    a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps(report))


if __name__=='__main__':main()
