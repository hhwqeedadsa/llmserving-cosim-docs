"""Independent tiny-fixture MRT1 decoder, endpoint/ACK and byte-memory oracle.

This accepts inline put/fetch-add and declared local stages only. It neither
imports production decoding nor purports to validate arbitrary transactions/5.
"""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
import struct
from fixtures.context_transactions.stage import load,save,sha


def audit(result,config):
    assert result['passed'] and result['drained'] and not result['pending_operations']
    tx=result['transactions'];net=tx['transport'];assert tx['duplicate_requests']==0
    assert not any(tx[k] for k in ('gpu_visibility_modeled','hardware_calibrated','roce_wire_equivalent'))
    connections={c['id']:c for c in config['connections']}
    specs={o['id']:o for o in config['operations']};rows={o['id']:o for o in tx['operations']}
    local={o['id']:o for o in result.get('local_work',[])}
    assert set(rows)=={i for i,o in specs.items() if o['kind']=='remote'}
    assert len(rows)==len(tx['operations'])==result['completed_operations']
    assert set(local)=={i for i,o in specs.items() if o['kind']=='local'}
    source=net['source_packets'];payload=net['source_payload_packets'];assert len(source)==len(payload)
    content={};retransmitted=0
    for row,body in zip(source,payload):
        stamp,sid,src,dst,nic,seq,size,uid=row;c=connections[sid//2]
        assert (src,dst,nic)==((c['dst'],c['src'],c['destination_nic']) if sid%2 else (c['src'],c['dst'],c['source_nic']))
        data=bytes.fromhex(body['hex']);assert (stamp,sid,seq,size)==(body['time_ns'],body['stream'],body['sequence'],len(data))
        stream=content.setdefault(sid,{})
        if seq in stream:retransmitted+=1
        for j,byte in enumerate(data):
            assert seq+j not in stream or stream[seq+j]==byte
            stream[seq+j]=byte
    capture=result['packet_capture'];assert capture['complete'] and not capture['omitted_events']
    header=capture['wire_headers'];events=[]
    for row,index in zip(capture['events'],header['event_header_indices']):
        e=dict(zip(capture['columns'],row));h=dict(zip(header['columns'],header['headers'][index]))
        assert e['packet_uid']==h['packet_uid'] and e['ip_protocol']==h['ip_protocol']
        e['wire_port']=h['source_port'];assert h['source_port']==h['destination_port']
        events.append(e)
    frames={}
    for s in net['streams']:
        sid=s['id'];raw=content.get(sid,{})
        assert set(raw)==set(range(s['submitted_bytes']))
        assert s['snd_nxt']==s['snd_una']==s['published_bytes']==s['submitted_bytes'] and s['qp_creations']==1
        data=bytes(raw[i] for i in range(len(raw)));offset=0
        c=connections[sid//2];src,dst=(c['dst'],c['src']) if sid%2 else (c['src'],c['dst'])
        sn,dn=(c['destination_nic'],c['source_nic']) if sid%2 else (c['source_nic'],c['destination_nic'])
        port=c['response_port'] if sid%2 else c['request_port']
        packets=[p for p in source if p[1]==sid]
        endpoint=[e for e in events if e['wire_port']==port and e['ip_protocol']==17]
        expected=Counter((p[0],p[7],p[5],p[6]) for p in packets)
        actual=Counter((e['time_ns'],e['packet_uid'],e['sequence'],e['data_payload_bytes']) for e in endpoint if e['node']==src and e['event']==0)
        assert actual==expected
        assert all(e['port']==sn for e in endpoint if e['node']==src and e['event']==0)
        arrivals={}
        for e in endpoint:
            if e['node']!=dst or e['event']!=1:continue
            assert e['port']==dn
            assert any(p[7]==e['packet_uid'] and p[5]==e['sequence'] and p[6]==e['data_payload_bytes'] and p[0]<=e['time_ns'] for p in packets)
            for i in range(e['sequence'],e['sequence']+e['data_payload_bytes']):arrivals.setdefault(i,e['time_ns'])
        assert set(arrivals)==set(raw)
        while offset<len(data):
            magic,verb,seq,address,key,size=struct.unpack_from('>IIQQII',data,offset)
            assert magic==0x4d525431 and verb in (1,2,3) and size>0
            end=offset+32+size;body=data[offset+32:end];assert len(body)==size and (sid,seq) not in frames
            works=[w for w in net['work'] if w['stream']==sid and (w['begin'],w['end'])==(offset,end)];assert len(works)==1
            w=works[0];accepted=max(arrivals[i] for i in range(end));assert w['accepted_ns']==accepted
            # Native cumulative ACK must actually depart destination and reach source.
            ack_rx=[e for e in events if e['wire_port']==port and e['ip_protocol']==252 and e['event']==1 and e['node']==src and e['sequence']>=end]
            assert ack_rx and w['acked_ns']==min(e['time_ns'] for e in ack_rx)
            for ack in ack_rx:
                assert ack['port']==sn and any(e['packet_uid']==ack['packet_uid'] and e['event']==0 and e['node']==dst and
                    e['port']==dn and accepted<=e['time_ns']<=ack['time_ns'] for e in events)
            frames[sid,seq]=dict(verb=verb,address=address,key=key,body=body,work=w);offset=end
        assert offset==len(data)
    assert len(frames)==len(net['work'])
    for i,r in rows.items():
        op=specs[i];frame=frames[r['connection']*2,r['sequence']]
        assert (frame['verb'],frame['address'],frame['key'],frame['body'])==(1 if op['verb']=='put' else 2,op['address'],op['key'],
            bytes.fromhex(op['hex']) if op['verb']=='put' else op['value'].to_bytes(8,'big'))
        w=frame['work'];assert (r['published_ns'],r['applied_ns'],r['request_acked_ns'])==(w['published_ns'],w['accepted_ns'],w['acked_ns'])
        if op['verb']=='fetch_add':
            reply=frames[2*r['connection']+1,r['sequence']]
            assert reply['verb']==3 and reply['key']==reply['address']==0 and len(reply['body'])==8
            assert int.from_bytes(reply['body'],'big')==r['returned_value']
            assert reply['work']['ready_ns']==r['applied_ns'] and reply['work']['accepted_ns']==r['response_ns']
        else:assert r['response_ns']==-1
        previous=[v['completed_ns'] for v in rows.values() if v['connection']==r['connection'] and v['sequence']<r['sequence']]
        assert r['completed_ns']==max(r['request_acked_ns'],r['response_ns'],*previous)
    allrows={**rows,**local}
    for i,row in allrows.items():
        op=specs[i];ready=max((allrows[d['operation']][d['event']+'_ns'] for d in op['after']),default=0)
        assert row['ready_ns']==ready
        if op['kind']=='local':
            assert row['rank']==op['rank'] and row['start_ns']==ready and row['completed_ns']==ready+op['duration_ns']
    # Independent full byte arrays are small in this fixture (4 KiB).
    memory={(w['owner'],w['key']):bytearray(w['bytes']) for w in config['windows']}
    for w in config['windows']:
        assert w['initialization']=='zero_fill/1' and not w['segments']
    target=tx['target_events'];assert len(target)==len(rows)
    seen=set()
    for order,e in enumerate(target):
        cid,seq=e['connection'],e['sequence'];assert (cid,seq) not in seen and not e['duplicate'] and e['order']==order;seen.add((cid,seq))
        r=next(v for v in rows.values() if (v['connection'],v['sequence'])==(cid,seq));f=frames[2*cid,seq]
        assert (e['address'],e['key'],e['verb'],e['body_hex'],e['time_ns'])==(f['address'],f['key'],f['verb'],f['body'].hex(),r['applied_ns'])
        owner=connections[cid]['dst'];window=next(w for w in config['windows'] if (w['owner'],w['key'])==(owner,e['key']))
        data=memory[owner,e['key']];offset=e['address']-window['address'];body=f['body']
        if f['verb']==2:
            old=int.from_bytes(data[offset:offset+8],'little');assert old==e['old_value']==r['returned_value']
            body=((old+int.from_bytes(body,'big'))%(2**64)).to_bytes(8,'little')
        data[offset:offset+len(body)]=body
    assert len(tx['windows'])==len(memory)
    for w in tx['windows']:
        actual=bytearray(w['bytes']);occupied=set()
        for page in w['pages']:
            data=bytes.fromhex(page['hex']);offset=page['offset'];assert not occupied.intersection(range(offset,offset+len(data)))
            occupied.update(range(offset,offset+len(data)));actual[offset:offset+len(data)]=data
        assert actual==memory[w['owner'],w['key']]
    assert net['closed'] and net['physical_drained'] and net['inflight_packets']==0
    return dict(passed=True,remote_operations=len(rows),local_operations=len(local),decoded_frames=len(frames),
        source_packets=len(source),retransmitted_packets=retransmitted,target_applications=len(target),finish_ns=result['finish_ns'],
        atomic_returns=[r['returned_value'] for r in rows.values() if 'returned_value' in r])


def tamper(result,program,mapping):
    from moe_ub_cosim.context_transactions import check_result
    rejected=[]
    changes={
        'operation_identity':lambda r:r['transactions']['operations'][0].update(connection=1),
        'negative_ack':lambda r:r['transactions']['operations'][0].update(request_acked_ns=-1),
        'missing_atomic_response':lambda r:next(o for o in r['transactions']['operations'] if o['verb']=='fetch_add').update(response_ns=-1),
        'duplicate_operation':lambda r:r['transactions']['operations'].append(deepcopy(r['transactions']['operations'][0])),
        'incomplete_local':lambda r:r['local_work'][0].update(completed_ns=-1),
        'extra_qp':lambda r:r['transactions']['transport']['streams'][0].update(qp_creations=2),
        'wrong_nic':lambda r:r['transactions']['transport']['streams'][0].update(source_nic=2),
        'wrong_source_packet':lambda r:r['transactions']['transport']['source_packets'][0].__setitem__(4,2),
        'truncated_capture':lambda r:r['packet_capture'].update(complete=False),
        'invalid_wire_index':lambda r:r['packet_capture']['wire_headers']['event_header_indices'].__setitem__(0,-1),
        'undrained':lambda r:r['transactions']['transport'].update(inflight_packets=1),
    }
    for label,change in changes.items():
        damaged=deepcopy(result);change(damaged)
        try:check_result(dict(program=program,mapping=mapping),damaged)
        except ValueError:rejected.append(label)
        else:raise AssertionError('tampered result accepted: '+label)
    return rejected


def oracle_tamper(result,program):
    def remove_ack(r):
        c=r['packet_capture'];cols=c['columns'];node=cols.index('node');event=cols.index('event');proto=cols.index('ip_protocol')
        row=next(e for e in c['events'] if e[event]==1 and e[node]==0 and e[proto]==252);row[node]=31
    changes={
        'duplicate_target':lambda r:r['transactions']['target_events'].append(deepcopy(r['transactions']['target_events'][0])),
        'atomic_return':lambda r:next(o for o in r['transactions']['operations'] if o['verb']=='fetch_add').update(returned_value=18),
        'target_memory':lambda r:r['transactions']['windows'][0]['pages'][0].update(hex='15'+r['transactions']['windows'][0]['pages'][0]['hex'][2:]),
        'missing_ack_rx':remove_ack,
    }
    rejected=[]
    for label,change in changes.items():
        damaged=deepcopy(result);change(damaged)
        try:audit(damaged,program)
        except AssertionError:rejected.append(label)
        else:raise AssertionError('independent oracle accepted tamper: '+label)
    return rejected


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();s=a.stage
    reports={}
    for label,plan in (('healthy','plan'),('fault','fault-plan')):
        result=load(s/label/'result.json');program=load(s/plan/'transactions.json');mapping=load(s/plan/'bindings.json')
        reports[label]=audit(result,program);reports[label]['tamper_rejected']=tamper(result,program,mapping)
        reports[label]['independent_oracle_tamper_rejected']=oracle_tamper(result,program)
        assert reports[label]['atomic_returns']==[17]
        assert len(mapping['bindings'])==4 and len({r['transaction_connection'] for r in mapping['bindings']})==4
        rows={r['id']:r for r in result['transactions']['operations']}
        assert rows[5]['published_ns']==rows[0]['completed_ns']+20000
        assert result['fault_drops']==(label=='fault') and reports[label]['retransmitted_packets']==(label=='fault')
        if label=='fault':
            assert max(rows[i]['completed_ns'] for i in (1,2,3))<80000==next(e[0] for e in result['transactions']['transport']['recovery']['events'] if e[1]==2)
            assert rows[0]['applied_ns']>80000
        libraries=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
        frozen={Path(p).name:h for p,h in load(s/'identity.json')['libraries'].items()}
        assert len(libraries)==8 and {p.name for p in libraries}==set(frozen)
        assert all(sha(s/'runtime/lib'/p.name)==frozen[p.name] for p in libraries)
        if label=='fault':
            recorded_runtime=Path(load(s/label/'inputs.json')['command'][0]).parent
            assert all(p.parent==recorded_runtime/'lib' for p in libraries)
    assert not a.output.exists();save(a.output,dict(passed=True,cases=reports,
        scope='tiny explicit context bindings, inline MRT1 frames, memory and actual endpoint/ACK evidence; not GIN/GPU timing'))


if __name__=='__main__':main()
