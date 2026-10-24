"""Independent stdlib packet/frame/memory replay for effect-order evidence."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import struct

def read(p):return json.loads(p.read_text())
def audit(result, work):
    assert result['passed'] and result['drained'] and result['fault_injection']['drop_counts']==[1]
    tx=result['transactions'];net=tx['transport'];capture=result['packet_capture']
    assert net['closed'] and net['physical_drained'] and capture['complete']
    assert not tx['gpu_visibility_modeled'] and not tx['hardware_calibrated']
    events=[dict(zip(capture['columns'],r)) for r in capture['events']]
    wire=capture['wire_headers']
    headers=[dict(zip(wire['columns'],wire['headers'][i])) for i in wire['event_header_indices']]
    assert len(headers)==len(events)
    bindings={c['id']:c for c in work['connections']};buffers={};arrivals={};rx_packets={};drops=0
    packets=net['source_packets'];payloads=net['source_payload_packets'];assert len(packets)==len(payloads)
    for values,payload in zip(packets,payloads):
        p=dict(zip(net['source_packet_columns'],values));body=bytes.fromhex(payload['hex'])
        assert (payload['stream'],payload['sequence'],payload['time_ns'],len(body))==(
            p['stream'],p['sequence'],p['time_ns'],p['payload_bytes'])
        c=bindings[p['stream']//2];reverse=p['stream']%2
        src,dst=(c['dst'],c['src']) if reverse else (c['src'],c['dst'])
        port=c['response_port' if reverse else 'request_port']
        assert (p['src'],p['dst'])==(src,dst)
        txs=[(e,h) for e,h in zip(events,headers) if e['packet_uid']==p['packet_uid'] and e['event']==0 and e['node']==src]
        assert len(txs)==1
        e,h=txs[0];assert e['time_ns']==p['time_ns'] and e['sequence']==p['sequence']
        assert h['source_port']==h['destination_port']==port and h['ip_protocol']==17
        rx=[e for e in events if e['packet_uid']==p['packet_uid'] and e['node']==dst and e['event']==1]
        lost=[e for e in events if e['packet_uid']==p['packet_uid'] and e['node']==dst and e['event']==2]
        assert len(rx)+len(lost)==1
        drops+=len(lost)
        buf=buffers.setdefault(p['stream'],{})
        if rx:rx_packets.setdefault(p['stream'],[]).append((rx[0]['time_ns'],p['sequence'],len(body)))
        for i,byte in enumerate(body,p['sequence']):
            assert i not in buf or buf[i]==byte
            buf[i]=byte
    assert drops==1
    ignored_out_of_order=0
    for stream,received in rx_packets.items():
        expected=0;times=arrivals.setdefault(stream,{})
        for stamp,sequence,size in sorted(received):
            if sequence>expected:
                ignored_out_of_order+=1;continue
            if sequence+size<=expected:continue
            assert sequence==expected
            for i in range(sequence,sequence+size):times[i]=stamp
            expected+=size
        assert expected==len(buffers[stream])
    frames={}
    for stream,buf in buffers.items():
        data=bytes(buf[i] for i in range(len(buf)));off=0
        while off<len(data):
            magic,verb,sequence,address,key,size=struct.unpack_from('>IIQQII',data,off)
            assert magic==0x4d525431 and verb in [1,2,3] and 0<size<=32768
            end=off+32+size;body=data[off+32:end];assert len(body)==size
            assert (stream,sequence) not in frames
            frames[stream,sequence]=dict(verb=verb,address=address,key=key,body=body,
                time_ns=max(arrivals[stream][i] for i in range(off,end)))
            off=end
    windows={(w['owner'],w['key']):(w['address'],bytearray(w['bytes'])) for w in work['windows']}
    for w in work['windows']:
        start,data=windows[w['owner'],w['key']]
        for s in w['segments']:
            b=bytes.fromhex(s['hex']);data[s['offset']:s['offset']+len(b)]=b
    captured={};orders=[]
    mixed=[('remote',e) for e in tx['target_events']]+[('local',e) for e in tx['local_events']]+[
        ('copy',e) for e in tx['local_memory_events']]
    timeline=sorted(mixed,key=lambda row:row[1]['order'])
    for kind,e in timeline:
        orders.append(e['order'])
        if kind=='remote':
            f=frames[e['connection']*2,e['sequence']]
            assert not e['duplicate'] and (e['verb'],e['address'],e['key'],bytes.fromhex(e['body_hex']),e['time_ns'])==(
                f['verb'],f['address'],f['key'],f['body'],f['time_ns'])
            c=bindings[e['connection']];base,data=windows[c['dst'],f['key']];offset=f['address']-base
            if f['verb']==1:data[offset:offset+len(f['body'])]=f['body']
            else:
                old=int.from_bytes(data[offset:offset+8],'little');assert old==e['old_value']
                value=(old+int.from_bytes(f['body'],'big'))%(2**64)
                data[offset:offset+8]=value.to_bytes(8,'little')
        elif kind=='local':
            for condition in e['conditions']:
                base,data=windows[e['owner'],condition['key']];offset=condition['address']-base
                actual=int.from_bytes(data[offset:offset+8],'little')
                assert actual==condition['observed_value']==condition['value']==2
        elif e['kind']=='captured':
            s=e['source'];base,data=windows[s['owner'],s['key']];offset=s['address']-base
            actual=bytes(data[offset:offset+s['bytes']]);assert actual.hex()==e['hex']
            captured[e['operation']]=actual
        elif e['kind']=='committed':
            assert captured[e['operation']].hex()==e['hex']
            target=e['target'];base,data=windows[target['owner'],target['key']]
            off=target['address']-base;b=captured[e['operation']];data[off:off+len(b)]=b
    assert len(orders)==len(set(orders))
    for w in tx['windows']:
        actual=bytearray(w['bytes'])
        for page in w['pages']:
            b=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(b)]=b
        assert actual==windows[w['owner'],w['key']][1]
    return dict(source_packets=len(packets),actual_endpoint_tx_witnesses=len(packets),
        decoded_frames=len(frames),actual_drops=drops,ignored_out_of_order=ignored_out_of_order,
        copy_hex=captured[5].hex(),
        target_times=[e['time_ns'] for e in tx['target_events']])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.runtime.resolve();rows={}
    for phase,names in [('before',['unsafe','isolated','wait']),('after',['isolated','wait']),
                        ('edges',['same-carrier','indirect-completion'])]:
        for name in names:
            r=read(root/phase/name/'result.json');w=read(root/phase/(name+'.json'))
            row=audit(r,w);rows[phase+'/'+name]=row
            assert row['copy_hex']==('11'*8 if name=='unsafe' else '22'*8)
    unchanged=[]
    for name in ['isolated','wait']:
        before=(root/'before'/name/'result.json').read_bytes();after=(root/'after'/name/'result.json').read_bytes()
        assert before==after;unchanged.append(name)
    original=read(root/'before/unsafe/result.json');w=read(root/'before/unsafe.json');rejected=[]
    for name in ['fake-fresh-copy','fabricated-application-time','false-notification']:
        altered=deepcopy(original)
        if name=='fake-fresh-copy':
            next(e for e in altered['transactions']['local_memory_events'] if e['kind']=='captured')['hex']='22'*8
        elif name=='fabricated-application-time':altered['transactions']['target_events'][-1]['time_ns']-=1
        else:altered['transactions']['local_events'][0]['conditions'][0]['observed_value']=1
        try:audit(altered,w)
        except AssertionError:rejected.append(name)
        else:raise AssertionError('accepted tampering: '+name)
    result=dict(passed=True,cases=rows,byte_identical_safe_results=unchanged,tamper_rejected=rejected,
        scope='functional model correctness; not a production vulnerability or MoE performance claim')
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
