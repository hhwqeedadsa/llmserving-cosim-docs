"""Decode actual wire bytes and replay independent Python RC endpoints offline."""
import argparse
from collections import Counter, deque
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'python'))
from moe_ub_cosim.rc_reference import Request, Response, RcRequester, RcResponder

NAMES={6:'WRITE_FIRST',7:'WRITE_MIDDLE',8:'WRITE_LAST',10:'WRITE_ONLY',20:'FETCH_ADD'}


def body(raw):
    """Independent big-endian unpacking; never calls the C++ encoder/parser."""
    assert len(raw)>=16 and len(raw)%4==0
    opcode,flags,pkey,qpn,sequence=struct.unpack_from('!BBHII',raw)
    assert not flags&0xcf and pkey==65535 and 0<qpn<=0xffffff and not sequence&0x7f000000
    psn=sequence&0xffffff;ack=bool(sequence>>31);pad=flags>>4&3
    assert psn<0xffffff and raw[-4:]==bytes(4)
    end=len(raw)-4-pad
    assert raw[end:len(raw)-4]==bytes(pad)
    if opcode in (17,18):
        assert qpn==1 and not ack and not pad
        aeth=struct.unpack_from('!I',raw,12)[0];syndrome=aeth>>24
        assert (syndrome==0 or opcode==17 and syndrome==0x60)
        assert end==(24 if opcode==18 else 16)
        old=struct.unpack_from('!Q',raw,16)[0] if opcode==18 else None
        return Response(psn,'ATOMIC_ACK' if opcode==18 else 'SEQ_NAK' if syndrome else 'ACK',old)
    assert qpn==2 and opcode in NAMES
    fields=dict(psn=psn,opcode=NAMES[opcode],ack_requested=ack)
    if opcode==20:
        address,key,addend,compare=struct.unpack_from('!QIQQ',raw,12)
        assert compare==0 and not pad and end==40
        return Request(**fields,address=address,key=key,addend=addend)
    pos=12
    if opcode in (6,10):
        address,key,length=struct.unpack_from('!QII',raw,pos);pos+=16
        fields.update(address=address,key=key,length=length)
    assert end>pos
    return Request(**fields,payload=raw[pos:end])


def datagram(raw):
    assert raw[:2]==bytes.fromhex('0021') and raw[2]==0x45
    total=struct.unpack_from('!H',raw,4)[0]
    assert total+2==len(raw) and raw[11]==17
    src,dst=struct.unpack_from('!II',raw,14)
    sport,dport,length,checksum=struct.unpack_from('!HHHH',raw,22)
    assert sport in (12000,13000) and dport==4791 and checksum==0 and length==len(raw)-22
    decoded=body(raw[30:]);assert (sport==13000)==isinstance(decoded,Response)
    return src,dst,decoded,raw[30:]


def verify_case(t,c,result):
    assert result['passed'] and result['drained'] and result['native_network_executed']
    rc=result['rc_reference'];assert rc['drained'] and not rc['hardware_calibrated']
    requester=RcRequester(mtu=c['mtu'],atomic_depth=1)
    for op in c['operations']:
        requester.post(op['kind'],op['address'],**(dict(data=bytes.fromhex(op['data_hex']))
            if op['kind']=='write' else dict(addend=op['addend'])))
    responder=RcResponder(bytes.fromhex(c['memory_hex']),mtu=c['mtu'],replay_slots=1)
    expected_packets={};responses=deque();completions=[];sent_packets=Counter();received_packets=Counter()
    previous_time=-1
    for event in rc['endpoint_events']:
        assert previous_time<=event['time_ns'];previous_time=event['time_ns']
        packet=body(bytes.fromhex(event['body_hex']));kind=event['kind']
        if kind=='request_sent':
            assert isinstance(packet,Request)
            if packet.psn not in expected_packets:
                # Sequential admission must wait for the previous real feedback.
                assert not requester.snapshot()['source_held']
                batch=requester.send();assert batch
                expected_packets.update((p.psn,p) for p in batch)
            assert expected_packets[packet.psn]==packet
            assert packet in requester.retransmit()
            sent_packets[event['packet_uid']]+=1
        elif kind=='request_received':
            assert isinstance(packet,Request)
            assert event['expected_psn']==responder.expected
            response=responder.receive(packet)
            if response is not None:responses.append(response)
            received_packets[event['packet_uid']]+=1
        elif kind=='response_enqueued':
            assert responses and packet==responses.popleft()
            sent_packets[event['packet_uid']]+=1
        elif kind=='response_received':
            assert isinstance(packet,Response)
            state=requester.receive(packet)
            if state=='completed':
                snapshot=requester.snapshot();i=sum(op['completed'] for op in snapshot['operations'])-1
                done=dict(operation=i,completed_ns=event['time_ns'],last_psn=snapshot['operations'][i]['last_psn'])
                if packet.opcode=='ATOMIC_ACK':done['old_value']=packet.old_value
                completions.append(done)
            received_packets[event['packet_uid']]+=1
        else:raise AssertionError('unknown endpoint event')
    assert not responses and not requester.snapshot()['source_held']
    assert all(op['completed'] for op in requester.snapshot()['operations'])
    assert bytes(responder.memory).hex()==rc['memory_hex'] and responder.effects==rc['target_effects']
    assert responder.expected==rc['target_next_psn'] and completions==rc['operations']
    # Check both endpoints against the declared operation values, independently
    # of source metadata captured in result.json.
    expected=bytearray.fromhex(c['memory_hex']);atomic_old=[]
    for op in c['operations']:
        start=op['address']
        if op['kind']=='write':
            data=bytes.fromhex(op['data_hex']);expected[start:start+len(data)]=data
        else:
            old=int.from_bytes(expected[start:start+8],'little');atomic_old.append(old)
            expected[start:start+8]=((old+op['addend'])%(1<<64)).to_bytes(8,'little')
    assert expected==responder.memory
    assert atomic_old==[r['old_value'] for r in completions if 'old_value' in r]

    links={}
    for link in t['links']:
        links[link['src'],link['src_port']]=(link['dst'],link['dst_port'],link['delay_ns'])
        links[link['dst'],link['dst_port']]=(link['src'],link['src_port'],link['delay_ns'])
    in_flight={};last_tx={};source=Counter();sink=Counter();tx_bytes=0;drops=[];per_node=Counter();counts=Counter()
    port_stats={};physical_capture=Counter();packet_values={}
    endpoint_sent=Counter();endpoint_received=Counter();previous_time=-1
    by_uid={}
    for event in rc['physical_packets']:
        assert previous_time<=event['time_ns'];previous_time=event['time_ns']
        raw=bytes.fromhex(event['packet_hex']);sip,dip,packet,wire_body=datagram(raw)
        uid=event['packet_uid'];node=event['node'];port=event['port'];kind=event['kind']
        packet_values[uid]=packet
        if uid in by_uid:assert by_uid[uid]==(sip,dip,wire_body)
        else:by_uid[uid]=(sip,dip,wire_body)
        counts[kind]+=1
        physical_capture[event['time_ns'],node,port,dict(tx=0,rx=1,drop=2)[kind],uid,len(raw)]+=1
        if kind=='tx':
            peer,peer_port,delay=links[node,port]
            assert uid not in in_flight
            in_flight[uid]=(peer,peer_port,event['time_ns'],delay,len(raw))
            tx_bytes+=len(raw);per_node[node]+=len(raw);last_tx[uid]=node
            stats=port_stats.setdefault((node,port),Counter());stats['wire']+=len(raw)
            if isinstance(packet,Request):stats['data']+=len(raw);stats['packets']+=1
            else:stats['control']+=len(raw)
            if node in (c['src'],c['dst']):
                source[type(packet).__name__]+=1;endpoint_sent[uid]+=1
        else:
            assert kind in ('rx','drop') and uid in in_flight
            peer,peer_port,start,delay,size=in_flight.pop(uid)
            assert (node,port)==(peer,peer_port) and size==len(raw)
            assert event['time_ns']>=start+delay
            if kind=='drop':
                rule=c['loss'];assert rule and rule['node']==node and rule['psn']==packet.psn and raw[30]==rule['opcode']
                drops.append(event)
            elif node in (c['src'],c['dst']):
                assert node==(c['src'] if isinstance(packet,Response) else c['dst'])
                sink[type(packet).__name__]+=1;endpoint_received[uid]+=1
    assert not in_flight and counts['tx']==counts['rx']+counts['drop']
    assert endpoint_sent==sent_packets and endpoint_received==received_packets
    assert len(drops)==rc['drops']==int(c['loss'] is not None)
    capture=result['packet_capture'];assert capture['complete'] and capture['omitted_events']==0
    captured_physical=Counter();queue={};queue_rows=[]
    for row in capture['events']:
        e=dict(zip(capture['columns'],row));p=packet_values[e['packet_uid']]
        assert e['message_id']==-1 and e['ip_protocol']==17 and e['sequence']==p.psn
        assert e['data_payload_bytes']==(len(p.payload) if isinstance(p,Request) else 0)
        if e['event']<3:
            captured_physical[e['time_ns'],e['node'],e['port'],e['event'],e['packet_uid'],e['model_bytes']]+=1
        else:
            assert e['event'] in (3,4)
            state=queue.setdefault((e['node'],e['port']),dict(bytes=0,time=0,area=0,peak=0,changes=0))
            state['area']+=state['bytes']*(e['time_ns']-state['time']);state['time']=e['time_ns']
            if e['event']==3:
                assert state['bytes']+e['model_bytes']==e['egress_queue_bytes']
                state['bytes']+=e['model_bytes'];state['peak']=max(state['peak'],state['bytes'])
            else:
                assert state['bytes']==e['egress_queue_bytes'] and state['bytes']>=e['model_bytes']
                state['bytes']-=e['model_bytes']
            state['changes']+=1
            queue_rows.append([e[k] for k in ('time_ns','node','port','egress_queue_bytes')])
    assert captured_physical==physical_capture and queue_rows==result['queue_events']
    for port in result['ports']:
        key=port['node'],port['port'];stats=port_stats.get(key,Counter())
        assert (port['model_tx_bytes'],port['data_tx_bytes'],port['control_tx_bytes'],port['data_packets'])==(
            stats['wire'],stats['data'],stats['control'],stats['packets'])
        if 'queue_metrics' in port:
            state=queue.get(key,dict(bytes=0,time=0,area=0,peak=0,changes=0))
            assert state['bytes']==0
            assert port['queue_metrics']==dict(area_byte_ns=state['area'],peak_bytes=state['peak'],
                                              final_bytes=0,changes=state['changes'])
    assert sum(p['model_tx_bytes'] for p in result['ports'])==tx_bytes
    assert sum(p['model_tx_bytes'] for p in result['ports'] if p['node'] in (c['src'],c['dst']))==sum(
        len(bytes.fromhex(e['packet_hex'])) for e in rc['physical_packets'] if e['kind']=='tx' and e['node'] in (c['src'],c['dst']))
    assert result['queue_events'] and result['finish_ns']>=max(r['completed_ns'] for r in completions)
    # Every endpoint response is UDP/BTH/AETH on the reverse fabric path. It is
    # never another data Request requiring its own ACK.
    assert all(isinstance(p,Response) for _,_,p,_ in map(datagram,[bytes.fromhex(e['packet_hex'])
        for e in rc['physical_packets'] if e['kind']=='tx' and e['node']==c['dst']]))
    return dict(passed=True,finish_ns=result['finish_ns'],operations=len(completions),
        request_packets=source['Request'],response_packets=source['Response'],
        physical_tx=counts['tx'],physical_rx=counts['rx'],physical_drop=counts['drop'],
        physical_tx_bytes=tx_bytes,timeouts=rc['timeouts'],target_effects=len(responder.effects),
        atomic_old_values=atomic_old,atomic_effects=sum(e['kind']=='fetch_add' for e in responder.effects),
        queue_events=len(result['queue_events']),memory_sha256=hashlib.sha256(responder.memory).hexdigest())


def verify(root):
    inputs=root/'inputs';t=json.loads((inputs/'topology/topology.json').read_bytes());summary={}
    for name in ('healthy','drop_write_middle','drop_write_ack','drop_atomic_ack'):
        c=json.loads((inputs/(name+'.json')).read_bytes());r=json.loads((root/name/'result.json').read_bytes())
        summary[name]=verify_case(t,c,r)
    legacy=json.loads((root/'legacy/result.json').read_bytes())
    assert legacy['passed'] and legacy['drained'] and len(legacy['messages'])==1
    summary['legacy']=dict(passed=True,messages=1,finish_ns=legacy['finish_ns'])
    return dict(passed=True,cases=summary,scope='functional RC packets and independent state replay; no performance/production/MoE equivalence claim')


def mutations(root):
    c=json.loads((root/'inputs/healthy.json').read_bytes());t=json.loads((root/'inputs/topology/topology.json').read_bytes())
    r=json.loads((root/'healthy/result.json').read_bytes());bad=[]
    x=copy.deepcopy(r);x['rc_reference']['operations'][-1]['old_value']+=1;bad.append(x)
    x=copy.deepcopy(r);x['rc_reference']['memory_hex']='ff'+x['rc_reference']['memory_hex'][2:];bad.append(x)
    x=copy.deepcopy(r);x['rc_reference']['physical_packets'][0]['port']=999;bad.append(x)
    x=copy.deepcopy(r);x['rc_reference']['endpoint_events'][0]['body_hex']='ff'+x['rc_reference']['endpoint_events'][0]['body_hex'][2:];bad.append(x)
    x=copy.deepcopy(r);x['rc_reference']['target_effects'].append(x['rc_reference']['target_effects'][-1]);bad.append(x)
    for x in bad:
        try:verify_case(t,c,x)
        except (AssertionError,KeyError,ValueError,struct.error):pass
        else:raise AssertionError('evidence mutation accepted')
    return len(bad)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert not a.output.exists();value=verify(a.run);value['mutations_rejected']=mutations(a.run)
    a.output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n');print('independent wire/state verification passed')
