"""Independent full-plan check using actual delivered bytes and target events."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

if not __debug__:
    raise RuntimeError("Legacy audit requires Python assertions; do not run with -O")
import struct


def decode(data):
    rows=[];offset=0
    while offset<len(data):
        magic,verb,seq,address,key,size=struct.unpack_from('>IIQQII',data,offset)
        assert magic==0x4d525431 and verb in (1,4,5) and 0<size<=32768
        assert offset+32+size<=len(data) and (verb==1 or size==4)
        if verb==5:assert not address and not key
        rows.append((seq,verb,address,key,data[offset+32:offset+32+size],offset,offset+32+size))
        offset+=32+size
    return rows


def verify_complete_ids(rows,expected):
    ids=[row[0] for row in rows]
    assert len(ids)==len(expected) and set(ids)==set(expected)


def verify_packet_bytes(net,capture,endpoints):
    """Verify actual TX and contiguous RX prefixes, even for a failed run.

    This function does not establish application/task completion. The full
    audit separately requires every stream, operation, event and memory slot.
    """
    assert capture['complete'] and capture['omitted_events']==0
    columns={name:i for i,name in enumerate(capture['columns'])}
    physical=defaultdict(list)
    for row in capture['events']:
        assert len(row)==len(columns)
        physical[row[columns['packet_uid']]].append(row)

    def captured(uid,event,node,nic,stream,sequence,size,before,exact=False,after=0):
        return any(row[columns['event']]==event and row[columns['node']]==node and
            row[columns['port']]==nic and row[columns['message_id']]==stream and
            row[columns['ip_protocol']]==17 and row[columns['sequence']]==sequence and
            row[columns['data_payload_bytes']]==size and
            row[columns['time_ns']]>=after and
            (row[columns['time_ns']]==before if exact else row[columns['time_ns']]<=before)
            for row in physical[uid])

    source=defaultdict(dict);source_bytes=duplicate_bytes=0;by_uid={}
    packets_per_stream=Counter();bytes_per_stream=Counter()
    assert len(net['source_packets'])==len(net['source_payload_packets'])
    for packet,payload in zip(net['source_packets'],net['source_payload_packets']):
        t,stream,src,dst,nic,seq,size,uid=packet;data=bytes.fromhex(payload['hex'])
        assert stream in endpoints and uid not in by_uid and size>0 and seq>=0 and t>=0
        assert (t,stream,seq)==(payload['time_ns'],payload['stream'],payload['sequence']) and len(data)==size
        assert (src,dst,nic)==endpoints[stream][:3]
        assert captured(uid,0,src,nic,stream,seq,size,t,exact=True)
        by_uid[uid]=(t,stream,seq,data);source_bytes+=size
        packets_per_stream[stream]+=1;bytes_per_stream[stream]+=size
        for offset,byte in enumerate(data,seq):
            if offset in source[stream]:
                assert source[stream][offset]==byte;duplicate_bytes+=1
            source[stream][offset]=byte
    assert len(net['streams'])==len(endpoints) and {s['id'] for s in net['streams']}==set(endpoints)
    for s in net['streams']:
        assert (s['src'],s['dst'],s['source_nic'],s['destination_nic'])==endpoints[s['id']]
        assert s['source_data_packets']==packets_per_stream[s['id']]
        assert s['source_payload_bytes']==bytes_per_stream[s['id']]
    delivered=defaultdict(bytearray);delivery_times=defaultdict(list);delivered_packets=0
    for e in net['selective_repeat']['events']:
        if e['kind']!='payload_delivered':continue
        stream=e['stream'];begin=len(delivered[stream]);data=bytes.fromhex(e['hex'])
        assert begin==e['sequence'] and len(data)>0 and e['sack_valid']
        assert (e['sack_begin'],e['sack_end'])==(begin,begin+len(data))
        uid=e['packet_uid'];sent_t,sent_stream,sent_seq,sent_data=by_uid[uid]
        assert (sent_stream,sent_seq,sent_data)==(stream,begin,data) and sent_t<=e['time_ns']
        _,destination,_,nic=endpoints[stream]
        assert captured(uid,1,destination,nic,stream,begin,len(data),e['time_ns'],after=sent_t)
        delivered[stream].extend(data);delivery_times[stream].append((begin,begin+len(data),e['time_ns']))
        delivered_packets+=1
    assert source_bytes-sum(map(len,source.values()))==duplicate_bytes
    return source,delivered,delivery_times,dict(transmitted_payload_bytes=source_bytes,
        unique_transmitted_payload_bytes=sum(map(len,source.values())),duplicate_payload_bytes=duplicate_bytes,
        source_packets=len(by_uid),verified_delivered_packets=delivered_packets,
        delivered_prefix_bytes=sum(map(len,delivered.values())),complete_application_proven=False)


def audit(work,result,protocol):
    assert result['passed'] and result['drained'] and result['gpu_count'] in (128,256,512)
    legacy=result['legacy'];net=legacy['transport'];ops=work['operations'];events=work['events'];rows=legacy['operations']
    ranks=work['ranks'];experts=work['experts'];local=experts//ranks;mapping=work['rank_to_gpu']
    assert len(rows)==len(ops) and legacy['finished'] and legacy['source_capture_leases_remaining']==0
    assert legacy['local_operations_bypass_posting_queue'] is True
    assert net['closed'] and net['physical_drained'] and net['inflight_packets']==0
    assert net['recovery']['complete'] and not net['recovery']['failed'] and net['recovery']['omitted_events']==0
    assert len(legacy['contexts'])==ranks*experts
    contexts={};stream_context={};remote_index=0
    for s in range(ranks):
        for d in range(ranks):
            for e in range(local):
                context=(s*ranks+d)*local+e;remote=s!=d
                expected=remote_index if remote else -1
                row=legacy['contexts'][context]
                assert row[:4]==[s,d,e,expected] and row[4]==row[5]==row[6]==row[7]==row[8] and row[9]
                if not remote:assert row[4:9]==[0]*5
                contexts[context]=(s,d,e,expected)
                if remote:
                    stream_context[2*remote_index]=context;stream_context[2*remote_index+1]=context;remote_index+=1
    assert len(net['streams'])==2*remote_index==work['limits']['streams']
    endpoints={}
    for stream,context in stream_context.items():
        s,d,_,_=contexts[context]
        endpoints[stream]=(mapping[d],mapping[s],1,1) if stream%2 else (mapping[s],mapping[d],1,1)
    source,delivered,delivery_times,packet_summary=verify_packet_bytes(net,result['packet_capture'],endpoints)
    source_bytes=packet_summary['transmitted_payload_bytes']
    frames={};logical_bytes=0
    assert set(source)==set(delivered)
    for stream,values in source.items():
        assert sorted(values)==list(range(len(values)))
        data=bytes(values[i] for i in range(len(values)));assert data==bytes(delivered[stream]);logical_bytes+=len(data)
        for seq,verb,address,key,body,begin,end in decode(data):
            identity=(stream,seq);assert identity not in frames
            t=next(t for a,b,t in delivery_times[stream] if a<end<=b)
            frames[identity]=(verb,address,key,body,begin,end,t)
    boundaries={(w['stream'],w['begin'],w['end']):w for w in net['work']}
    assert len(boundaries)==len(frames)==work['limits']['work']
    assert len(net['work'])==len(boundaries) and len({w['id'] for w in net['work']})==len(boundaries)
    assert sum(s['submitted_bytes'] for s in net['streams'])==logical_bytes
    for s in net['streams']:
        assert s['qp_creations']==1 and s['submitted_bytes']==s['published_bytes']==s['snd_una']
        assert s['accepted_work']==s['ack_completed_work'] and s['published_bytes']==len(delivered.get(s['id'],b''))
    indices=Counter();per_context=Counter();by_context_seq={};op_slot={};prior_completed={}
    data_groups={(o[1],o[2],o[4],o[5],o[6]) for o in ops if o[3]=='data'}
    expected_remote_requests=expected_remote_replies=0
    for i,(op,row) in enumerate(zip(ops,rows)):
        identity,call,phase,kind,s,d,expert,token,size,value,refs,index=op
        context=(s*ranks+d)*local+expert%local;assert row[0]==context
        assert row[1]==per_context[context];per_context[context]+=1
        k=(call,phase,s,d,expert);ordinal=indices[k] if kind=='data' else 0
        if kind=='data':indices[k]+=1
        assert index==ordinal
        op_slot[i]=(phase,ordinal if kind=='data' else 'counter')
        by_context_seq[(context,row[1])]=i
        gates=[prior_completed.get(context,0)]
        assert row[5]>=0
        if s!=d:
            assert row[4]>=0 and row[5]>=row[4]
            assert row[2]+1==row[3] and row[2]==row[1]
            gates.append(row[6])
            stream=2*contexts[context][3];request=frames[(stream,row[1])]
            assert request[0]==(1 if kind=='data' else 4) and request[2]==1
            assert request[6]==row[5]
            target=boundaries[(stream,request[4],request[5])]
            assert target['published_ns']==row[4] and target['accepted_ns']==row[5] and target['acked_ns']==row[6]
            expected_remote_requests+=1
            if kind!='data':
                expected_remote_replies+=1;reply=frames[(stream+1,row[1])]
                assert reply[0]==5 and len(reply[3])==4 and int.from_bytes(reply[3],'big')==row[9]
                assert reply[6]==row[7]
                reply_work=boundaries[(stream+1,reply[4],reply[5])]
                assert reply_work['published_ns']==row[5] and reply_work['accepted_ns']==row[7]
            else:assert row[7]==-1 and row[9] is None
        else:
            assert row[2:5]==[-1,-1,-1] and row[6:8]==[-1,-1] and row[9] is None
            gates.append(row[5])
        if s!=d and kind!='data':gates.append(row[7])
        assert row[8]==max(gates);prior_completed[context]=row[8]
        assert net['physical_drained'] and row[8]<=result['finish_ns']
    assert len(frames)==expected_remote_requests+expected_remote_replies
    # Replay byte effects, clean events and expert copies by one global order.
    ordered=[(order,'graph',event,t) for order,event,t in legacy['event_order']]
    ordered.extend((e[0],'memory',e,e[1]) for e in legacy['memory_events']);ordered.sort()
    assert [o[0] for o in ordered]==list(range(len(ordered)))
    assert len(legacy['memory_events'])==len(ops)
    assert len(legacy['event_order'])==len(events)
    assert all(ordered[i][3]<=ordered[i+1][3] for i in range(len(ordered)-1))
    fired=set();time_rows=legacy['event_times_ns'];memory={};expert_outputs={};expert_calls=Counter();applied=set()
    assert len(time_rows)==len(events)
    control_reads=zero_counts=zero_flags=0
    hidden=work['hidden_size'];dispatch_bytes=work['dispatch_bytes'];max_tokens=work['max_tokens_per_rank']
    stride=((max_tokens*dispatch_bytes+4+4095)//4096)*4096
    for _,kind,record,t in ordered:
        if kind=='graph':
            event=record;name,rank,deps,op_id,attrs=events[event]
            assert event not in fired and all(d in fired for d in deps) and time_rows[event]==t
            if name=='clean_next_buffer':
                phase=attrs['buffer_index']
                memory={k:v for k,v in memory.items() if not (contexts[k[0]][1]==rank and k[1]==phase)}
            elif name=='expert_stage_done':
                call=expert_calls[rank];expert_calls[rank]+=1
                for i,op in enumerate(ops):
                    if op[1:4]==[call,0,'data'] and op[5]==rank:
                        context=rows[i][0];data=memory[(context,0,op[11])]
                        expert_outputs[(call,rank,op[4],op[6],op[7])]=data[16:16+2*hidden]
            elif op_id is not None:
                op=ops[op_id];row=rows[op_id]
                if name=='issued':
                    if op[4]!=op[5]:assert t<=row[4]
                    else:assert t==row[5]
                elif name=='accepted':assert op_id in applied and t==row[5]
                elif name=='sender_done':assert t==row[8]
                elif name=='visible':
                    assert op_id in applied
                    if op[3]!='data':
                        assert int.from_bytes(memory[(row[0],op[2],'counter')],'little')==op[9]%(2**32);control_reads+=1
            fired.add(event)
        else:
            _,_,context,seq,verb,address,hex_body,duplicate,old=record
            i=by_context_seq[(context,seq)];op=ops[i];row=rows[i];assert i not in applied and not duplicate and row[5]==t
            _,call,phase,opkind,s,d,expert,token,size,value,refs,index=op;body=bytes.fromhex(hex_body)
            expected_address=(context+1)*2*stride+phase*stride+(index*dispatch_bytes if opkind=='data' else max_tokens*dispatch_bytes)
            assert address==expected_address and verb==(1 if opkind=='data' else 4)
            if s!=d:assert frames[(2*contexts[context][3],seq)][1:4]==(address,1,body)
            if opkind=='data':
                assert len(body)==size
                if phase==0:assert body==bytes((s*17+token*13+expert*7+call*11+j)%251 for j in range(size))
                else:assert body==expert_outputs[(call,s,d,expert,token)]
                memory[(context,phase,index)]=body
            else:
                prior=int.from_bytes(memory.get((context,phase,'counter'),bytes(4)),'little')
                assert len(body)==4 and int.from_bytes(body,'big')==value%(2**32) and prior==old
                if s!=d:assert old==row[9]
                else:assert row[9] is None
                next_value=(prior+int.from_bytes(body,'big'))%(2**32) if s!=d else int.from_bytes(body,'big')
                memory[(context,phase,'counter')]=next_value.to_bytes(4,'little')
                zero_counts+=opkind=='count' and value==-1
                zero_flags+=opkind=='flag' and (call,phase,s,d,expert) not in data_groups
            applied.add(i)
    assert len(applied)==len(ops) and len(fired)==len(events)
    verify_complete_ids(legacy['counters'],contexts)
    for context,base,saved_stride,counters in legacy['counters']:
        assert base==(context+1)*2*stride and saved_stride==stride
        assert counters==[int.from_bytes(memory.get((context,p,'counter'),bytes(4)),'little') for p in range(2)]
    verify_complete_ids(legacy['final_data'],{i for i,o in enumerate(ops) if o[3]=='data'})
    for i,saved in legacy['final_data']:
        op=ops[i];assert bytes.fromhex(saved)==memory.get((rows[i][0],op[2],op[11]),bytes(op[8]))
    # Batch4 is a source publication boundary; verify before any TX, never a CQ.
    publication=defaultdict(int);published_operations=set()
    for t,i,context,eligible,requested,previous,exclusive in legacy['publications']:
        op=ops[i];row=rows[i];assert previous==publication[context]
        assert op[4]!=op[5] and i not in published_operations;published_operations.add(i)
        assert eligible==(op[3]!='data' or (op[11]+1)%4==0)
        assert exclusive==(max(previous,requested) if eligible else previous)
        assert requested==row[3] and time_rows[op[10][0]]==t
        publication[context]=exclusive
    assert published_operations=={i for i,o in enumerate(ops) if o[4]!=o[5]}
    if protocol.get('faults'):
        assert result['fault_injection']['drop_counts']==[rule['count'] for rule in protocol['faults']]
    # No injected fault does not imply no retransmission: queues or ACK timing
    # can cause legitimate retries. Verify actual byte duplication in all cases.
    assert source_bytes-logical_bytes==packet_summary['duplicate_payload_bytes']
    memory_rows=[(list(k),v.hex()) for k,v in sorted(memory.items(),key=lambda row:(row[0][0],row[0][1],str(row[0][2])))]
    memory_hash=hashlib.sha256(json.dumps(memory_rows,sort_keys=True).encode()).hexdigest()
    return dict(passed=True,operations=len(ops),events=len(events),contexts=len(contexts),streams=len(net['streams']),
        logical_transport_bytes=logical_bytes,transmitted_payload_bytes=source_bytes,retransmitted_payload_bytes=source_bytes-logical_bytes,
        zero_token_count_operations=zero_counts,zero_token_flag_operations=zero_flags,actual_notification_reads=control_reads,
        last_kernel_complete_ns=max(time_rows[i] for i,e in enumerate(events) if e[0]=='kernel_complete'),
        physical_finish_ns=result['finish_ns'],final_memory_sha256=memory_hash,packet_verification=packet_summary,
        route_source_verified=False,hardware_calibrated=False,numerical_experts_executed=False)


def run(directory, protocol, output):
    """Audit complete retained native execution; route provenance is separate."""
    directory,protocol,output=map(lambda p:Path(p).resolve(),(directory,protocol,output))
    if output.exists() or not output.parent.is_dir():
        raise ValueError('Legacy audit output must be new with an existing parent')
    def load(path,maximum):
        if not path.is_file() or path.stat().st_size>maximum:
            raise ValueError('Missing or oversized legacy audit input: '+str(path))
        content=path.read_bytes()
        if len(content)>maximum:raise ValueError('Legacy audit input grew while reading')
        return json.loads(content)
    summary=audit(load(directory/'execution.json',32*2**20),
                  load(directory/'result.json',256*2**20),load(protocol,65536))
    summary['input_sha256']={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in (directory/'execution.json',directory/'result.json',protocol)}
    summary['schema_version']='moe-legacy-native-audit/1'
    summary['scope']='Complete functional network execution; does not verify route source, numerical experts or GPU timing'
    output.write_text(json.dumps(summary,indent=2)+'\n')
    return summary
