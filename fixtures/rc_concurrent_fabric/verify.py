"""Independent wire decode, per-QP reference and shared target-memory replay."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

from moe_ub_cosim.rc_reference import Request, Response, RcRequester, RcResponder

NAMES={6:'WRITE_FIRST',7:'WRITE_MIDDLE',8:'WRITE_LAST',10:'WRITE_ONLY',20:'FETCH_ADD'}
CASES=('healthy','drop_write_middle','drop_atomic_ack')


def load(path):return json.loads(path.read_bytes())


def body(raw):
    assert len(raw)>=16 and len(raw)%4==0
    opcode,flags,pkey,qpn,word=struct.unpack_from('!BBHII',raw)
    assert flags&0xcf==0 and pkey==65535 and 0<qpn<=0xffffff and not word&0x7f000000
    psn=word&0xffffff;ack=bool(word>>31);pad=flags>>4&3
    assert psn<0xffffff and raw[-4:]==bytes(4)
    end=len(raw)-pad-4;assert raw[end:len(raw)-4]==bytes(pad)
    if opcode in (17,18):
        assert qpn%2==1 and not ack and not pad
        aeth=struct.unpack_from('!I',raw,12)[0];syndrome=aeth>>24
        assert syndrome==0 or opcode==17 and syndrome==0x60
        assert end==(24 if opcode==18 else 16)
        old=struct.unpack_from('!Q',raw,16)[0] if opcode==18 else None
        packet=Response(psn,'ATOMIC_ACK' if opcode==18 else 'SEQ_NAK' if syndrome else 'ACK',old)
        return (qpn-1)//2,packet,qpn
    assert qpn%2==0 and opcode in NAMES
    values=dict(psn=psn,opcode=NAMES[opcode],ack_requested=ack)
    if opcode==20:
        address,key,addend,compare=struct.unpack_from('!QIQQ',raw,12)
        assert compare==0 and pad==0 and end==40
        packet=Request(**values,address=address,key=key,addend=addend)
    else:
        pos=12
        if opcode in (6,10):
            address,key,length=struct.unpack_from('!QII',raw,pos);pos+=16
            values.update(address=address,key=key,length=length)
        assert end>pos;packet=Request(**values,payload=raw[pos:end])
    return (qpn-2)//2,packet,qpn


def datagram(raw):
    assert raw[:2]==bytes.fromhex('0021') and raw[2]==0x45 and raw[11]==17
    assert struct.unpack_from('!H',raw,4)[0]+2==len(raw)
    sip,dip=struct.unpack_from('!II',raw,14)
    sport,dport,length,checksum=struct.unpack_from('!HHHH',raw,22)
    cid,packet,qpn=body(raw[30:])
    assert dport==4791 and checksum==0 and length==len(raw)-22
    assert sport==(13000 if isinstance(packet,Response) else 12000)+cid
    return cid,packet,qpn,sip,dip,raw[30:]


def verify_case(topology,work,result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    value=result['rc_concurrent'];assert value['drained'] and not value['hardware_calibrated']
    connections={q['id']:q for q in work['connections']}
    states={q['id']:q for q in value['connections']}
    assert set(states)==set(connections)
    memory={m['gpu']:bytearray.fromhex(m['data_hex']) for m in work['memories']}
    requesters={};responders={};expected_packets={};replies={};completions={};sends={}
    for cid,q in connections.items():
        req=RcRequester(mtu=work['mtu'],atomic_depth=1)
        for op in q['operations']:
            req.post(op['kind'],op['address'],**(dict(data=bytes.fromhex(op['data_hex']))
                if op['kind']=='write' else dict(addend=op['addend'])))
        response=RcResponder(bytes(memory[q['dst']]),mtu=work['mtu'],replay_slots=1)
        # Public memory image is shared; sequencing and replay stay per QP.
        response.memory=memory[q['dst']]
        requesters[cid]=req;responders[cid]=response;expected_packets[cid]={}
        replies[cid]=deque();completions[cid]=[];sends[cid]=Counter()
        assert states[cid]['source_qpn']==2*cid+1 and states[cid]['destination_qpn']==2*cid+2
        assert states[cid]['source_port']==12000+cid and states[cid]['src']==q['src'] and states[cid]['dst']==q['dst']
    expected_effects=[];endpoint_sent=Counter();endpoint_received=Counter();endpoint_body={};times={}
    previous=-1
    for event in value['endpoint_events']:
        assert previous<=event['time_ns'];previous=event['time_ns']
        raw=bytes.fromhex(event['body_hex']);cid,packet,qpn=body(raw)
        assert cid==event['connection'] and cid in connections
        requester=requesters[cid];responder=responders[cid];kind=event['kind'];uid=event['packet_uid']
        if uid in endpoint_body:assert endpoint_body[uid]==raw
        else:endpoint_body[uid]=raw
        if kind=='request_sent':
            assert isinstance(packet,Request)
            if packet.psn not in expected_packets[cid]:
                assert not requester.snapshot()['source_held']
                batch=requester.send();assert batch
                expected_packets[cid].update((p.psn,p) for p in batch)
            assert expected_packets[cid][packet.psn]==packet and packet in requester.retransmit()
            sends[cid][packet.psn]+=1;endpoint_sent[uid]+=1
            times.setdefault(cid,event['time_ns'])
        elif kind=='request_received':
            assert isinstance(packet,Request) and event['expected_psn']==responder.expected
            before=len(responder.effects);reply=responder.receive(packet)
            expected_effects.extend(dict(row,connection=cid) for row in responder.effects[before:])
            if reply is not None:replies[cid].append(reply)
            endpoint_received[uid]+=1
        elif kind=='response_enqueued':
            assert isinstance(packet,Response) and replies[cid] and packet==replies[cid].popleft()
            endpoint_sent[uid]+=1
        elif kind=='response_received':
            assert isinstance(packet,Response)
            status=requester.receive(packet)
            if status=='completed':
                snapshot=requester.snapshot();index=sum(o['completed'] for o in snapshot['operations'])-1
                done=dict(operation=index,completed_ns=event['time_ns'],last_psn=snapshot['operations'][index]['last_psn'])
                if packet.opcode=='ATOMIC_ACK':done['old_value']=packet.old_value
                completions[cid].append(done)
            endpoint_received[uid]+=1
        else:raise AssertionError('unknown RC endpoint event')
    assert expected_effects==value['target_effects']
    assert {m['gpu']:m['data_hex'] for m in value['memories']}=={gpu:data.hex() for gpu,data in memory.items()}
    retransmitted_wqes=0
    for cid,q in connections.items():
        snapshot=requesters[cid].snapshot()
        assert not replies[cid] and not snapshot['source_held'] and all(o['completed'] for o in snapshot['operations'])
        assert completions[cid]==states[cid]['operations'] and responders[cid].expected==states[cid]['target_next_psn']
        retries=sum(sends[cid][o['first_psn']]-1 for o in snapshot['operations'])
        assert retries==states[cid]['timeouts'];retransmitted_wqes+=retries
    links={};adjacency=defaultdict(set)
    for link in topology['links']:
        links[link['src'],link['src_port']]=(link['dst'],link['dst_port'],link['delay_ns'],link['rate_bps'])
        links[link['dst'],link['dst_port']]=(link['src'],link['src_port'],link['delay_ns'],link['rate_bps'])
        adjacency[link['src']].add(link['dst']);adjacency[link['dst']].add(link['src'])
    gpu_ids={r['id'] for r in topology['nodes'] if r['kind']=='gpu'}
    distances={}
    for dst in gpu_ids:
        dist={dst:0};queue=deque([dst])
        while queue:
            node=queue.popleft()
            for peer in adjacency[node]:
                if peer not in dist:
                    dist[peer]=dist[node]+1
                    if peer not in gpu_ids:queue.append(peer)
        distances[dst]=dist
    inflight={};counts=Counter();values={};capture=Counter();ports={};source_packets=Counter()
    physical_sent=Counter();physical_received=Counter();seen_bytes={};end_addresses={};source_qps=defaultdict(set)
    dropped=[];last_port_end={};previous=-1
    for event in value['physical_packets']:
        assert previous<=event['time_ns'];previous=event['time_ns']
        wire=bytes.fromhex(event['packet_hex']);cid,packet,qpn,sip,dip,raw=datagram(wire)
        assert cid in connections;q=connections[cid]
        feedback=isinstance(packet,Response);src=q['dst'] if feedback else q['src'];dst=q['src'] if feedback else q['dst']
        for gpu,address in ((src,sip),(dst,dip)):
            if gpu in end_addresses:assert end_addresses[gpu]==address
            else:end_addresses[gpu]=address
        uid=event['packet_uid'];kind=event['kind'];node=event['node'];port=event['port'];values[uid]=packet
        if uid in seen_bytes:assert seen_bytes[uid]==(sip,dip,raw)
        else:seen_bytes[uid]=(sip,dip,raw)
        counts[kind]+=1;capture[event['time_ns'],node,port,dict(tx=0,rx=1,drop=2)[kind],uid,len(wire)]+=1
        if kind=='tx':
            peer,peer_port,delay,rate=links[node,port]
            assert uid not in inflight and distances[dst][peer]==distances[dst][node]-1
            duration=(len(wire)*8*10**9)//rate
            assert event['time_ns']>=last_port_end.get((node,port),0)
            last_port_end[node,port]=event['time_ns']+duration
            inflight[uid]=(peer,peer_port,event['time_ns'],delay,duration,len(wire))
            stat=ports.setdefault((node,port),Counter());stat['wire']+=len(wire)
            if feedback:stat['control']+=len(wire)
            else:stat['data']+=len(wire);stat['packets']+=1
            if node in gpu_ids:
                assert node==src and raw==endpoint_body[uid]
                physical_sent[uid]+=1;source_packets['response' if feedback else 'request']+=1
                if not feedback:source_qps[node,port].add(cid)
        else:
            assert kind in ('rx','drop') and uid in inflight
            peer,peer_port,start,delay,duration,size=inflight.pop(uid)
            assert (node,port)==(peer,peer_port) and size==len(wire) and event['time_ns']>=start+delay+duration
            if kind=='drop':
                rule=work['loss'];assert rule and (cid,node,wire[30],packet.psn)==tuple(rule[k] for k in ('connection','node','opcode','psn'))
                dropped.append(event)
            elif node in gpu_ids:
                assert node==dst and raw==endpoint_body[uid];physical_received[uid]+=1
    assert not inflight and counts['tx']==counts['rx']+counts['drop']
    assert physical_sent==endpoint_sent and physical_received==endpoint_received
    assert len(dropped)==value['drops']==int(work['loss'] is not None)
    assert len(end_addresses)==len(set(end_addresses.values()))
    actual_capture=result['packet_capture'];assert actual_capture['complete'] and actual_capture['omitted_events']==0
    physical_capture=Counter();queues={};queue_rows=[]
    for row in actual_capture['events']:
        e=dict(zip(actual_capture['columns'],row));packet=values[e['packet_uid']]
        assert e['message_id']==-1 and e['ip_protocol']==17 and e['sequence']==packet.psn
        assert e['data_payload_bytes']==(len(packet.payload) if isinstance(packet,Request) else 0)
        if e['event']<3:
            physical_capture[e['time_ns'],e['node'],e['port'],e['event'],e['packet_uid'],e['model_bytes']]+=1
        else:
            assert e['event'] in (3,4)
            state=queues.setdefault((e['node'],e['port']),dict(bytes=0,time=0,area=0,peak=0,changes=0))
            state['area']+=state['bytes']*(e['time_ns']-state['time']);state['time']=e['time_ns']
            if e['event']==3:
                assert state['bytes']+e['model_bytes']==e['egress_queue_bytes']
                state['bytes']+=e['model_bytes'];state['peak']=max(state['peak'],state['bytes'])
            else:
                assert state['bytes']==e['egress_queue_bytes'] and state['bytes']>=e['model_bytes']
                state['bytes']-=e['model_bytes']
            state['changes']+=1;queue_rows.append([e[k] for k in ('time_ns','node','port','egress_queue_bytes')])
    assert physical_capture==capture and queue_rows==result['queue_events']
    for port in result['ports']:
        key=port['node'],port['port'];stat=ports.get(key,Counter())
        assert (port['model_tx_bytes'],port['data_tx_bytes'],port['control_tx_bytes'],port['data_packets'])==(
            stat['wire'],stat['data'],stat['control'],stat['packets'])
        if 'queue_metrics' in port:
            state=queues.get(key,dict(bytes=0,time=0,area=0,peak=0,changes=0));assert state['bytes']==0
            assert port['queue_metrics']==dict(area_byte_ns=state['area'],peak_bytes=state['peak'],final_bytes=0,changes=state['changes'])
    intervals=[(times[cid],1) for cid in connections]+[(rows[-1]['completed_ns'],-1) for rows in completions.values()]
    active=maximum=0
    for _,delta in sorted(intervals):active+=delta;maximum=max(maximum,active)
    assert maximum>=2 and max(map(len,source_qps.values()))>=2
    assert result['finish_ns']>=max(rows[-1]['completed_ns'] for rows in completions.values())
    return dict(passed=True,finish_ns=result['finish_ns'],connections=len(connections),operations=sum(map(len,completions.values())),
        concurrent_inflight_qps=maximum,max_source_qps_per_physical_port=max(map(len,source_qps.values())),
        physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'],
        request_packets=source_packets['request'],response_packets=source_packets['response'],timeouts=retransmitted_wqes,
        timeouts_by_connection={cid:states[cid]['timeouts'] for cid in connections},
        target_effects=len(expected_effects),atomic_effects=sum(e['kind']=='fetch_add' for e in expected_effects),
        atomic_arrival_order=[dict(connection=e['connection'],old=e['old']) for e in expected_effects
                              if e['kind']=='fetch_add'],
        completions=completions,memory_sha256={gpu:hashlib.sha256(data).hexdigest() for gpu,data in memory.items()},
        target3_counter=int.from_bytes(memory[3][3072:3080],'little'),target0_counter=int.from_bytes(memory[0][1024:1032],'little'),
        queue_events=len(queue_rows),packet_fields_independently_decoded=True,shared_target_replayed=True)


def execute(stage,output,case=None):
    topology=load(stage/'inputs/topology/topology.json');summary={}
    for name in ([case] if case else CASES):
        work=load(stage/'inputs'/(name+'.json'));result=load(stage/name/'result.json')
        summary[name]=verify_case(topology,work,result)
        assert summary[name]['target3_counter']==47 and summary[name]['target0_counter']==11
        assert summary[name]['atomic_effects']==4
        assert summary[name]['timeouts']==int(name!='healthy')
    rejected=[]
    if case is None:
        base=load(stage/'healthy/result.json');work=load(stage/'inputs/healthy.json')
        for kind in ('memory','atomic_return','connection','qpn','path','completion_time','duplicate_effect'):
            changed=deepcopy(base);value=changed['rc_concurrent']
            if kind=='memory':value['memories'][0]['data_hex']='ff'+value['memories'][0]['data_hex'][2:]
            elif kind=='atomic_return':value['connections'][0]['operations'][-1]['old_value']+=1
            elif kind=='connection':value['endpoint_events'][0]['connection']=(value['endpoint_events'][0]['connection']+1)%4
            elif kind=='qpn':value['connections'][0]['source_qpn']+=2
            elif kind=='path':value['physical_packets'][0]['port']=999
            elif kind=='completion_time':value['connections'][0]['operations'][-1]['completed_ns']-=1
            else:value['target_effects'].append(deepcopy(value['target_effects'][0]))
            try:verify_case(topology,work,changed)
            except (AssertionError,ValueError,KeyError):rejected.append(kind)
            else:raise AssertionError('tamper accepted: '+kind)
        build=load(stage/'build/backend.json')
        for name in CASES:
            loaded=load(stage/name/'result.json')['loaded_libraries'];actual={}
            for filename in loaded:
                path=Path(filename)
                if path.name.startswith('libns3'):
                    # Preserve original evidence paths on restoration. Check the
                    # recorded loader location against its recorded executable,
                    # then hash the staged copy, never a file at the old root.
                    assert path.parent==Path(build['backend']).parent/'runtime/lib'
                    assert path.name not in actual
                    actual[path.name]=hashlib.sha256((stage/'build/runtime/lib'/path.name).read_bytes()).hexdigest()
            assert actual==build['library_hashes']
        healthy=summary['healthy']['completions']
        for name,cid in [('drop_write_middle',0),('drop_atomic_ack',1)]:
            rows=summary[name]['completions'];fault_finish=rows[cid][-1]['completed_ns']
            assert summary[name]['timeouts_by_connection']=={q:int(q==cid) for q in rows}
            assert all(rows[other][-1]['completed_ns']<fault_finish for other in rows if other!=cid)
            assert fault_finish>healthy[cid][-1]['completed_ns']
    value=dict(passed=True,cases=summary,tamper_classes_rejected=rejected,
               source_of_order='actual endpoint arrival order; no imposed cross-QP atomic order',
               scope='functional multi-QP packets and independent shared-memory replay; no GPU/GIN calibration or innovation proof')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=CASES)
    args=parser.parse_args();execute(args.stage.resolve(),args.output.resolve(),args.case)
