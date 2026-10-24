"""Independent tiny mixed-service byte/queue/endpoint oracle, not GIN timing."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import struct
from fixtures.self_peer_service.stage import load,save,sha


def audit(r,cfg):
    assert r['passed'] and r['drained'] and not r['pending_operations']
    tx=r['transactions'];net=tx['transport'];connections={c['id']:c for c in cfg['connections']}
    specs={o['id']:o for o in cfg['operations']};rows={o['id']:o for o in tx['operations']};local={o['id']:o for o in r['local_work']}
    assert set(rows)=={i for i,o in specs.items() if o['kind']=='remote'} and len(rows)==r['completed_operations']==len(tx['operations'])
    assert set(local)=={i for i,o in specs.items() if o['kind']=='local'} and r['completed_local_work']==len(local)
    allrows={**rows,**local}
    for i,row in allrows.items():
        ready=max((allrows[d['operation']][d['event']+'_ns'] for d in specs[i]['after']),default=0)
        assert row['ready_ns']==ready
        if i in local:assert row['start_ns']==ready and row['completed_ns']==ready+specs[i]['duration_ns']
    selfids={i for i,c in connections.items() if 'self_service' in c};remote=set(connections)-selfids
    assert {s['id'] for s in net['streams']}=={2*i+j for i in remote for j in (0,1)}
    assert len(net['streams'])==2*len(remote)
    for cid in selfids:
        c=connections[cid];last=0
        for row in sorted((o for o in rows.values() if o['connection']==cid),key=lambda o:o['sequence']):
            start=max(row['published_ns'],last)
            assert row['delivery']=='declared_self_service/1' and row['self_started_ns']==start
            assert row['applied_ns']==start+c['self_service']['apply_ns']
            assert row['completed_ns']==row['self_completed_ns']==start+c['self_service']['complete_ns']
            assert row['request_acked_ns']==row['response_ns']==-1;last=row['completed_ns']
        summary=next(c for c in tx['connections'] if c['id']==cid)['self_service']
        assert summary['physical_packets']==0 and summary['hardware_qps_modeled'] is False and summary['resource_creations']==1
    capture=r['packet_capture'];assert capture['complete'] and not capture['omitted_events']
    h=capture['wire_headers'];events=[]
    assert len(capture['events'])==len(h['event_header_indices'])
    for raw,index in zip(capture['events'],h['event_header_indices']):
        e=dict(zip(capture['columns'],raw));header=dict(zip(h['columns'],h['headers'][index]))
        assert header['packet_uid']==e['packet_uid'] and header['source_port']==header['destination_port']
        e['wire_port']=header['source_port'];events.append(e)
    source=net['source_packets'];payload=net['source_payload_packets'];assert len(source)==len(payload)
    rawbytes={};duplicates=0
    for p,b in zip(source,payload):
        stamp,sid,src,dst,nic,seq,size,uid=p;assert sid//2 in remote
        c=connections[sid//2];assert (src,dst,nic)==((c['dst'],c['src'],c['destination_nic']) if sid%2 else (c['src'],c['dst'],c['source_nic']))
        body=bytes.fromhex(b['hex']);assert (stamp,sid,seq,size)==(b['time_ns'],b['stream'],b['sequence'],len(body))
        data=rawbytes.setdefault(sid,{});duplicates+=seq in data
        for j,byte in enumerate(body):assert seq+j not in data or data[seq+j]==byte;data[seq+j]=byte
    frames={}
    for s in net['streams']:
        sid=s['id'];c=connections[sid//2];data=rawbytes.get(sid,{})
        assert s['qp_creations']==1 and s['snd_una']==s['snd_nxt']==s['submitted_bytes']==s['published_bytes']
        assert set(data)==set(range(s['submitted_bytes']))
        port=c['response_port'] if sid%2 else c['request_port']
        src,dst=(c['dst'],c['src']) if sid%2 else (c['src'],c['dst'])
        sn,dn=(c['destination_nic'],c['source_nic']) if sid%2 else (c['source_nic'],c['destination_nic'])
        assert (s['src'],s['dst'],s['source_nic'],s['destination_nic'],s['wire_port'])==(src,dst,sn,dn,port)
        packets=[p for p in source if p[1]==sid]
        txs=[e for e in events if e['wire_port']==port and e['ip_protocol']==17 and e['node']==src and e['event']==0]
        assert Counter((p[0],p[7],p[5],p[6]) for p in packets)==Counter((e['time_ns'],e['packet_uid'],e['sequence'],e['data_payload_bytes']) for e in txs)
        assert all(e['port']==sn for e in txs)
        arrivals={}
        for e in events:
            if not(e['wire_port']==port and e['ip_protocol']==17 and e['node']==dst and e['event']==1):continue
            assert e['port']==dn and any(p[7]==e['packet_uid'] and p[0]<=e['time_ns'] and (p[5],p[6])==(e['sequence'],e['data_payload_bytes']) for p in packets)
            for j in range(e['sequence'],e['sequence']+e['data_payload_bytes']):arrivals.setdefault(j,e['time_ns'])
        assert set(arrivals)==set(data)
        raw=bytes(data[j] for j in range(len(data)));offset=0
        while offset<len(raw):
            magic,verb,seq,address,key,size=struct.unpack_from('>IIQQII',raw,offset);assert magic==0x4d525431 and verb in (1,2,3)
            end=offset+32+size;body=raw[offset+32:end];assert len(body)==size
            works=[w for w in net['work'] if (w['stream'],w['begin'],w['end'])==(sid,offset,end)];assert len(works)==1
            work=works[0];accepted=max(arrivals[j] for j in range(end));assert accepted==work['accepted_ns']
            acks=[e for e in events if e['wire_port']==port and e['node']==src and e['event']==1 and e['ip_protocol']==252 and e['sequence']>=end]
            assert acks and work['acked_ns']==min(e['time_ns'] for e in acks)
            ack=next(e for e in acks if e['time_ns']==work['acked_ns']);assert ack['port']==sn
            assert any(e['packet_uid']==ack['packet_uid'] and e['node']==dst and e['event']==0 and e['port']==dn and accepted<=e['time_ns']<=ack['time_ns'] for e in events)
            assert (sid,seq) not in frames;frames[sid,seq]=dict(verb=verb,address=address,key=key,body=body,work=work);offset=end
        assert offset==len(raw)
    assert len(frames)==len(net['work'])
    captured={e['operation']:bytes.fromhex(e['hex']) for e in tx['source_events'] if e['kind']=='captured'}
    for i,row in rows.items():
        op=specs[i]
        if row['connection'] in selfids:continue
        f=frames[2*row['connection'],row['sequence']]
        body=captured[i] if 'source' in op else bytes.fromhex(op['hex']) if op['verb']=='put' else op['value'].to_bytes(8,'big')
        assert (f['verb'],f['address'],f['key'],f['body'])==(1 if op['verb']=='put' else 2,op['address'],op['key'],body)
        assert (row['published_ns'],row['applied_ns'],row['request_acked_ns'])==(f['work']['published_ns'],f['work']['accepted_ns'],f['work']['acked_ns'])
        assert row['completed_ns']>=row['request_acked_ns']>=row['applied_ns']>=row['published_ns']
    memory={(w['owner'],w['key']):bytearray(w['bytes']) for w in cfg['windows']}
    assert all(not w['segments'] for w in cfg['windows'])
    windows={(w['owner'],w['key']):w for w in cfg['windows']};seen=set();leases={}
    memory_events=sorted([*tx['target_events'],*tx['source_events'],*tx['local_events']],key=lambda e:e['order'])
    assert [e['order'] for e in memory_events]==list(range(len(memory_events)))
    for e in memory_events:
        if 'operation' in e:
            row=rows[e['operation']]
            if 'conditions' in e:
                for condition in e['conditions']:
                    w=windows[condition['owner'],condition['key']];offset=condition['address']-w['address']
                    value=int.from_bytes(memory[condition['owner'],condition['key']][offset:offset+8],'little')
                    assert value==condition['observed_value']==condition['value'] and condition['matched'] and condition['compare']=='eq' and 'reset_to' not in condition
                assert e['time_ns']==row['ready_ns'];continue
            if e['kind']=='acquired':leases[e['operation']]=e;assert e['time_ns']==row['ready_ns']
            elif e['kind']=='released':assert e['operation'] in leases and e['time_ns']==row['completed_ns'];leases.pop(e['operation'])
            elif e['kind']=='captured':
                w=windows[e['owner'],e['key']];offset=e['address']-w['address']
                assert bytes.fromhex(e['hex'])==memory[e['owner'],e['key']][offset:offset+e['bytes']] and e['time_ns']==row['published_ns']
            else:
                # local predicate observations have no reset in these inputs.
                raise AssertionError('unsupported memory event')
            continue
        identity=e['connection'],e['sequence'];assert identity not in seen and not e['duplicate'];seen.add(identity)
        row=next(o for o in rows.values() if (o['connection'],o['sequence'])==identity);op=specs[row['id']];owner=connections[row['connection']]['dst']
        body=captured[row['id']] if 'source' in op else bytes.fromhex(op['hex']) if op['verb']=='put' else op['value'].to_bytes(8,'big')
        assert (e['address'],e['key'],e['verb'],e['body_hex'],e['time_ns'])==(op['address'],op['key'],1 if op['verb']=='put' else 2,body.hex(),row['applied_ns'])
        w=windows[owner,op['key']];data=memory[owner,op['key']];offset=op['address']-w['address']
        if op['verb']=='fetch_add':
            old=int.from_bytes(data[offset:offset+8],'little');assert old==e['old_value']==row['returned_value']
            body=((old+op['value'])%2**64).to_bytes(8,'little')
        data[offset:offset+len(body)]=body
    assert len(seen)==len(rows) and not leases and not tx['active_source_leases'] and tx['duplicate_requests']==0
    for w in tx['windows']:
        data=bytearray(w['bytes'])
        for page in w['pages']:raw=bytes.fromhex(page['hex']);data[page['offset']:page['offset']+len(raw)]=raw
        assert data==memory[w['owner'],w['key']]
    assert net['physical_drained'] and net['closed'] and net['inflight_packets']==0
    return dict(passed=True,self_operations=sum(o['connection'] in selfids for o in rows.values()),remote_operations=sum(o['connection'] in remote for o in rows.values()),
        actual_source_packets=len(source),retransmitted_packets=duplicates,target_applications=len(seen),finish_ns=r['finish_ns'],
        atomic_returns=[o['returned_value'] for o in rows.values() if 'returned_value' in o],
        final_values={str(owner):int.from_bytes(data[:8],'little') for (owner,key),data in memory.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();s=a.stage
    cfg=load(s/'inputs/mixed.json');reports={}
    for label in ('mixed','fault-online'):
        r=load(s/label/'result.json');reports[label]=audit(r,cfg)
        assert reports[label]['atomic_returns']==[5,6] and reports[label]['final_values']=={'0':8,'4':8}
        assert r['fault_drops']==(label=='fault-online') and reports[label]['retransmitted_packets']==(label=='fault-online')
        local=r['local_work'][0];selfrows=[o for o in r['transactions']['operations'] if 'self_started_ns' in o]
        assert all(local['start_ns']<=o['self_started_ns']<o['completed_ns']<local['completed_ns'] for o in selfrows)
        assert selfrows[2]['completed_ns']<selfrows[0]['completed_ns']<selfrows[1]['completed_ns']
    old=load(s/'inputs/default-result.json');new=load(s/'default/result.json')
    libraries=old.pop('loaded_libraries');current=new.pop('loaded_libraries');assert old==new
    frozen={Path(p).name:h for p,h in load(s/'build.json')['libraries'].items()}
    recorded_runtime=Path(load(s/'default/inputs.json')['command'][0]).parent
    for label in ('default','mixed','fault-online'):
        paths=[Path(p) for p in load(s/label/'result.json')['loaded_libraries'] if Path(p).name in frozen]
        assert len(paths)==8 and all(p.parent==recorded_runtime/'lib' for p in paths)
        assert all(sha(s/'runtime/lib'/name)==h for name,h in frozen.items())
    assert {Path(p).name for p in libraries if Path(p).name in frozen}==set(frozen)
    assert not a.output.exists();save(a.output,dict(passed=True,default_full_result_equal_except_library_paths=True,cases=reports,scope='explicit functional self-provider and actual remote bytes; no modern GIN/GPU timing'))


if __name__=='__main__':main()
