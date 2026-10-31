"""Independent input/memory/MRT1 packet/callback oracle; no simulator imports."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import struct


def load(path):return json.loads(Path(path).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def expected(work):
    windows={};addresses={}
    for w in work['windows']:
        ident=w['owner'],w['key'];addresses[ident]=w['address']
        data=bytearray(w['bytes'])
        for s in w['segments']:
            value=bytes.fromhex(s['hex']);data[s['offset']:s['offset']+len(value)]=value
        windows[ident]=data
    connections={c['id']:c for c in work['connections']}
    registrations={(r['owner'],r['nic'],r['key']):r['window_key'] for r in work.get('registrations',[])}
    requests={};old_values={};sequences=defaultdict(int)
    def area(owner,key,address,n):
        ident=owner,key;offset=address-addresses[ident]
        assert 0<=offset and offset+n<=len(windows[ident])
        return windows[ident],offset
    for op in work['operations']:
        local=op['kind']=='local';owner=op['rank'] if local else connections[op['connection']]['src']
        for wait in op.get('local_wait',[]):
            n=wait.get('bits',64)//8;data,start=area(owner,wait['key'],wait['address'],n)
            value=int.from_bytes(data[start:start+n],'little')
            assert value==wait['value'] if wait['compare']=='eq' else value>=wait['value']
            if 'reset_to' in wait:data[start:start+n]=wait['reset_to'].to_bytes(n,'little')
        if local:
            if 'copy' in op:
                s,t=op['copy']['source'],op['copy']['target'];n=s['bytes']
                src,a=area(owner,s['key'],s['address'],n);value=src[a:a+n]
                dst,b=area(t.get('owner',owner),t['key'],t['address'],n);dst[b:b+n]=value
            continue
        conn=connections[op['connection']];target=conn['dst'];atomic=op['verb']!='put'
        if atomic:
            n=4 if op['verb']=='fetch_add32' else 8;body=op['value'].to_bytes(n,'big');verb=4 if n==4 else 2
        else:
            if 'source' in op:
                source=op['source'];n=source['bytes'];data,a=area(owner,source['key'],source['address'],n);body=bytes(data[a:a+n])
            else:body=bytes.fromhex(op['hex']);n=len(body)
            verb=1
        key=registrations.get((target,conn['destination_nic'],op['key']),op['key'])
        data,a=area(target,key,op['address'],n)
        old=int.from_bytes(data[a:a+n],'little') if atomic else 0;old_values[op['id']]=old
        data[a:a+n]=((old+op['value'])%(1<<(n*8))).to_bytes(n,'little') if atomic else body
        seq=sequences[conn['id']];sequences[conn['id']]+=1
        requests[op['id']]=dict(connection=conn['id'],sequence=seq,verb=verb,address=op['address'],key=op['key'],body=body,
            self_service='self_service' in conn,old=old)
    return windows,requests


def audit(work_path,result_path,transcript=None):
    w=load(work_path);r=load(result_path);tx=r['transactions'];transport=tx['transport'];memory,requests=expected(w)
    assert r['passed'] and r['native_network_executed'] and r['drained'] and transport['physical_drained'] and transport['closed']
    assert r['gpu_count']==128 and r['switch_count']==20
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    assert len(tx['windows'])==len(memory)
    for item in tx['windows']:
        ident=item['owner'],item['key'];actual=bytearray(item['bytes'])
        for page in item['pages']:
            data=bytes.fromhex(page['hex']);actual[page['offset']:page['offset']+len(data)]=data
        assert actual==memory[ident]
    # Every source packet is checked against its actual captured payload. Replay
    # copies must have identical bytes; unique byte unions equal delivered data.
    sent=defaultdict(dict);delivered=defaultdict(dict);packet_keys=[]
    for packet in transport['source_packets']:
        time,stream,src,dst,nic,seq,n,uid=packet
        packet_keys.append((time,stream,seq,n))
    payload_keys=[]
    for p in transport['source_payload_packets']:
        data=bytes.fromhex(p['hex']);payload_keys.append((p['time_ns'],p['stream'],p['sequence'],len(data)))
        for i,byte in enumerate(data,p['sequence']):
            assert i not in sent[p['stream']] or sent[p['stream']][i]==byte
            sent[p['stream']][i]=byte
    assert sorted(packet_keys)==sorted(payload_keys)
    delivery_rows=[e for e in transport['selective_repeat']['events'] if e['kind']=='payload_delivered']
    for e in delivery_rows:
        data=bytes.fromhex(e['hex']);stream=e['stream']
        for i,byte in enumerate(data,e['sequence']):
            assert i not in delivered[stream]  # Exactly-once in-order payload application.
            delivered[stream][i]=byte
        assert sorted(delivered[stream])==list(range(len(delivered[stream])))
    assert dict(sent)==dict(delivered)
    decoded=[]
    for stream,rows in delivered.items():
        raw=bytes(rows[i] for i in range(len(rows)));offset=0
        while offset<len(raw):
            magic,verb,seq,address,key,n=struct.unpack_from('>IIQQII',raw,offset)
            assert magic==0x4d525431 and offset+32+n<=len(raw)
            body=raw[offset+32:offset+32+n];decoded.append((stream,verb,seq,address,key,body));offset+=32+n
        assert offset==len(raw)
    wanted=[]
    for op in requests.values():
        if not op['self_service']:
            wanted.append((2*op['connection'],op['verb'],op['sequence'],op['address'],op['key'],op['body']))
            if op['verb'] in (2,4):wanted.append((2*op['connection']+1,5 if op['verb']==4 else 3,op['sequence'],0,0,
                op['old'].to_bytes(4 if op['verb']==4 else 8,'big')))
    assert sorted(decoded)==sorted(wanted)
    actual=[(e['connection'],e['sequence'],e['verb'],e['address'],e['key'],bytes.fromhex(e['body_hex']),e['old_value'])
            for e in tx['target_events'] if not e['duplicate']]
    assert sorted(actual)==sorted((o['connection'],o['sequence'],o['verb'],o['address'],o['key'],o['body'],o['old']) for o in requests.values())
    callbacks=appends=0
    if transcript:
        seen=set();accepted=set();byid={op['id']:op for op in w['operations']}
        for row in (json.loads(line) for line in Path(transcript).read_text().splitlines()):
            message=row['message']
            if row['direction']=='native':
                for e in message['events']:
                    if 'operation' in e:seen.add((e['operation'],e['event']));callbacks+=1
            else:
                for op in message['submissions']:
                    assert op==byid[op['id']] and op['id'] not in accepted
                    assert all((d['operation'],d['event']) in seen for d in op['after'])
                    if op['after']:appends+=1
                    accepted.add(op['id'])
        assert accepted==set(byid) and appends>0
    max_work=max([op['completed_ns'] for op in tx['operations']]+[op['completed_ns'] for op in r.get('local_work',[])])
    selective=transport['selective_repeat']['events']
    return dict(passed=True,input_sha256=sha(work_path),result_sha256=sha(result_path),windows_checked=len(memory),
        logical_window_bytes_checked=sum(map(len,memory.values())),source_packet_captures=len(packet_keys),
        unique_transport_payload_bytes=sum(map(len,sent.values())),delivered_frames=len(decoded),
        self_target_requests=sum(o['self_service'] for o in requests.values()),target_operations_checked=len(actual),
        callbacks_checked=callbacks,dependent_appends_checked=appends,fault_drops=r['fault_drops'],
        fast_retransmits=sum(e['kind']=='fast_retransmit' for e in selective),task_completed_ns=max_work,
        network_drained_ns=r['finish_ns'],late_feedback_ns=max(e['time_ns'] for e in selective if e['kind']=='feedback_received'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('run','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();root=args.run.resolve();repo=Path(__file__).resolve().parents[2]
    result={}
    for name in ('normal','loss','compat'):
        work=repo/'examples/scaleout_rdma_transactions'/('selective-chain.json' if name=='compat' else 'scaleout-profile.json')
        result[name]=audit(work,root/name/'result.json',root/name/'control.jsonl' if name!='compat' else None)
    a,b=(load(root/name/'result.json') for name in ('normal','loss'))
    assert a['transactions']['windows']==b['transactions']['windows']
    assert result['normal']['fault_drops']==0 and result['loss']['fault_drops']==1 and result['loss']['fast_retransmits']==1
    assert 'execution_limits' not in load(root/'compat/result.json')['transactions']
    result.update(passed=True,new_networks=3,normal_loss_final_memory_identical=True,
        scope='artificial functional transactions on 128 endpoint Clos; no real MoE or hardware calibration')
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
