"""Independent stdlib endpoint, sequence, MRT1, memory and lease audit."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import struct

def read(p): return json.loads(p.read_text())
def audit(result, work):
    t=result.get('persistent_transport',result.get('transactions',{}).get('transport'))
    capture=result['packet_capture'];assert capture['complete']
    es=[dict(zip(capture['columns'],row)) for row in capture['events']]
    wire=capture['wire_headers']
    headers=[dict(zip(wire['columns'],wire['headers'][i])) for i in wire['event_header_indices']]
    assert len(headers)==len(es)
    failures={f['stream']:f for f in t['recovery']['failures']}
    assert not result['passed'] and failures and not t['closed'] and not t['physical_drained']
    assert not t['source_isolation']['failed_resources_released']
    streams={s['id']:s for s in t['streams']}
    cnp_origins={e['packet_uid']:e['stream'] for e in t.get('dcqcn_reference',{}).get('events',[])
                 if e['kind']=='cnp_generated'}
    packets=[dict(zip(t['source_packet_columns'],row)) for row in t['source_packets']]
    # Physical source TX is separate from the adapter's source-packet ledger.
    physical=[e for e in es if e['event']==0 and e['ip_protocol']==17 and e['node']<result['gpu_count']]
    assert len(physical)==len(packets)
    received={};by_tx={};payloads=t.get('source_payload_packets',[])
    if payloads:assert len(payloads)==len(packets)
    for i,p in enumerate(packets):
        s=streams[p['stream']]
        assert (p['src'],p['dst'],p['nic'])==(s['src'],s['dst'],s['source_nic'])
        if p['stream'] in failures:assert p['time_ns']<=failures[p['stream']]['time_ns']
        tx=[e for e in physical if (e['packet_uid'],e['time_ns'],e['node'],e['sequence'])==
            (p['packet_uid'],p['time_ns'],p['src'],p['sequence'])]
        assert len(tx)==1 and tx[0]['data_payload_bytes']==p['payload_bytes']
        endpoint=[e for e in es if e['packet_uid']==p['packet_uid'] and e['node']==p['dst'] and e['event'] in [1,2]]
        assert len(endpoint)==1
        if payloads:
            item=payloads[i];body=bytes.fromhex(item['hex'])
            assert (item['stream'],item['sequence'],item['time_ns'],len(body))==(
                p['stream'],p['sequence'],p['time_ns'],p['payload_bytes'])
            by_tx[p['packet_uid']]=body
        if endpoint[0]['event']==1:received.setdefault(p['stream'],[]).append((endpoint[0],p))
    states={s['stream']:s for s in t['recovery']['states']}
    rec=[dict(zip(t['recovery']['columns'],r)) for r in t['recovery']['events']]
    for stream,f in failures.items():
        rows=[e for e in rec if e['message_id']==stream and e['event']==4]
        assert len(rows)==1 and rows[0]['time_ns']==f['time_ns']
        assert states[stream]['failed'] and states[stream]['acknowledged_bytes']==rows[0]['acknowledged_bytes']
        assert states[stream]['deadline_ns']==-1
        for w in t['work']:
            if w['stream']==stream and w['ready_ns']>=f['time_ns']:assert w['published_ns']==-1
    terminal=t['source_isolation']['terminal_feedback'];assert t['source_isolation']['terminal_feedback_complete']
    assert t['source_isolation']['terminal_feedback_packets']==len(terminal)
    for feedback in terminal:
        f=failures[feedback['stream']];assert feedback['time_ns']>=f['time_ns'] and feedback['source']==f['source']
        actual=[e for e in es if (e['event'],e['time_ns'],e['packet_uid'],e['node'],e['ip_protocol'])==
            (1,feedback['time_ns'],feedback['packet_uid'],feedback['source'],feedback['ip_protocol'])]
        assert len(actual)==1
        if feedback['ip_protocol'] in [252,253]:assert actual[0]['sequence']==feedback['sequence']
    # Several QPs may share the same source NIC. Bind ACK/NACK with actual wire
    # ports; CnHeader has no UDP ports, so match the captured generated CNP UID.
    later=[]
    for e,h in zip(es,headers):
        if e['event']!=1 or e['ip_protocol'] not in [252,253,255]:continue
        if e['ip_protocol']==255:stream=cnp_origins.get(e['packet_uid'])
        else:
            matches=[i for i,s in streams.items() if e['node']==s['src'] and h['ports_present'] and
                     h['source_port']==h['destination_port']==s['wire_port']]
            assert len(matches)==1;stream=matches[0]
        if stream in failures and e['node']==failures[stream]['source'] and e['time_ns']>=failures[stream]['time_ns']:
            later.append(e)
    assert len(later)==len(terminal)
    assert t['source_isolation']['wire_idle'] and t['inflight_packets']==0
    if 'transactions' not in result:return dict(source_packets=len(packets),terminal_feedback=len(terminal),target_effects=0)
    # Independently emulate cumulative in-order delivery. Physical out-of-order
    # RX is not accepted data, even though it appears in packet capture.
    frames={}
    for stream,items in received.items():
        prefix=bytearray();arrival=[]
        for e,p in sorted(items,key=lambda pair:pair[0]['time_ns']):
            sequence=p['sequence'];body=by_tx[p['packet_uid']]
            if sequence!=len(prefix):continue
            prefix.extend(body);arrival.extend([e['time_ns']]*len(body))
        off=0
        while off+32<=len(prefix):
            magic,verb,seq,address,key,size=struct.unpack_from('>IIQQII',prefix,off)
            assert magic==0x4d525431 and verb in [1,2,3]
            end=off+32+size
            if end>len(prefix):break
            frames[stream,seq]=dict(verb=verb,address=address,key=key,body=bytes(prefix[off+32:end]),time_ns=arrival[end-1])
            off=end
    bindings={c['id']:c for c in work['connections']};windows={}
    for w in work['windows']:
        if 'hex' in w:data=bytearray.fromhex(w['hex'])
        else:
            data=bytearray(w['bytes'])
            for seg in w['segments']:
                b=bytes.fromhex(seg['hex']);data[seg['offset']:seg['offset']+len(b)]=b
        windows[w['owner'],w['key']]=(w['address'],data)
    tx=result['transactions'];seen=set()
    for e in tx['target_events']:
        identity=e['connection'],e['sequence'];assert identity not in seen and not e['duplicate'];seen.add(identity)
        f=frames[e['connection']*2,e['sequence']]
        assert (e['time_ns'],e['verb'],e['key'],e['address'],bytes.fromhex(e['body_hex']))==(
            f['time_ns'],f['verb'],f['key'],f['address'],f['body'])
        start,data=windows[bindings[e['connection']]['dst'],f['key']];offset=f['address']-start
        if f['verb']==1:data[offset:offset+len(f['body'])]=f['body']
        else:
            old=int.from_bytes(data[offset:offset+8],'little');assert old==e['old_value']
            value=(old+int.from_bytes(f['body'],'big'))%(2**64)
            data[offset:offset+8]=value.to_bytes(8,'little')
    for w in tx['windows']:
        if 'hex' in w:actual=bytes.fromhex(w['hex'])
        else:
            actual=bytearray(w['bytes'])
            for page in w['pages']:
                b=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(b)]=b
        start,expected=windows[w['owner'],w['key']];assert start==w['address'] and actual==expected
    leases={}
    for e in tx['source_events']:
        identity=e['operation']
        if e['kind']=='acquired':assert identity not in leases;leases[identity]=e
        elif e['kind']=='released':
            assert identity in leases;leases.pop(identity)
            op=next(o for o in tx['operations'] if o['id']==identity);assert op['completed_ns']==e['time_ns']
    assert set(leases)=={l['operation'] for l in tx['active_source_leases']}
    for op in tx['operations']:
        if op['connection']*2 in failures:assert op['request_acked_ns']==op['completed_ns']==-1
    return dict(source_packets=len(packets),terminal_feedback=len(terminal),target_effects=len(seen),active_leases=len(leases))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    a=p.parse_args();root=a.runtime.resolve();report={}
    for folder in ['accept','edges','late-submit-after','online-failure','shared-source']:
        for path in sorted((root/folder).glob('*/result.json')):
            r=read(path);t=r.get('persistent_transport',r.get('transactions',{}).get('transport',{}))
            if 'source_isolation' not in t or not t['recovery']['failed']:continue
            w=read(path.parent.parent/(path.parent.name+'-work.json'))
            report[str(path.relative_to(root))]=audit(r,w)
    assert len(report)==13
    sample=root/'accept/late-ack-target-effect/result.json';r=read(sample);w=read(sample.parent.parent/'late-ack-target-effect-work.json')
    rejected=[]
    for mutation in ['missing-feedback','ack-after-failure','wrong-target-bytes']:
        bad=deepcopy(r);tx=bad['transactions'];t=tx['transport']
        if mutation=='missing-feedback':t['source_isolation']['terminal_feedback'].pop()
        elif mutation=='ack-after-failure':t['recovery']['states'][0]['acknowledged_bytes']=1
        else:tx['windows'][1]['hex']='ff'+tx['windows'][1]['hex'][2:]
        try:audit(bad,w)
        except AssertionError:rejected.append(mutation)
        else:raise AssertionError('accepted tampering: '+mutation)
    (root/'independent-audit.json').write_text(json.dumps(dict(passed=True,cases=report,tampering_rejected=rejected),indent=2)+'\n')
    print(json.dumps(dict(passed=True,cases=len(report),source_packets=sum(v['source_packets'] for v in report.values()),tampering_rejected=len(rejected))))

if __name__=='__main__':main()
