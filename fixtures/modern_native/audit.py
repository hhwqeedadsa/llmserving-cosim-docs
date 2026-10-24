"""Independent tiny-workload packet, memory and modern-event acceptance oracle.

Does not import the new controller/layout or call its advancement logic. Dense
bytearrays below are bounded oracle storage, not a GPU capacity model.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
from pathlib import Path
import struct
from fixtures.modern_native.stage import load,save,sha


def memory_audit(result,initial,nodes):
    tx=result['transactions'];remote={o['id']:o for o in tx['operations']};local={o['id']:o for o in result['local_work']}
    conns={c['id']:c for c in initial['connections']};windows={(w['owner'],w['key']):w for w in initial['windows']}
    memory={k:bytearray(w['bytes']) for k,w in windows.items()}
    for k,w in windows.items():
        for segment in w['segments']:body=bytes.fromhex(segment['hex']);memory[k][segment['offset']:segment['offset']+len(body)]=body
    def read(owner,key,address,n):
        w=windows[owner,key];offset=address-w['address'];assert 0<=offset<=len(memory[owner,key])-n
        return bytes(memory[owner,key][offset:offset+n])
    source_leases={};local_leases={};copies={};source_bytes={};seen=set()
    def overlap(a,b):return a['owner']==b['owner'] and a['key']==b['key'] and a['address']<b['address']+b['bytes'] and b['address']<a['address']+a['bytes']
    def write(owner,key,address,body,exclude=None):
        location=dict(owner=owner,key=key,address=address,bytes=len(body))
        assert all(not overlap(location,l) for i,l in source_leases.items() if i!=exclude)
        assert all(not overlap(location,l) for i,ls in local_leases.items() if i!=exclude for l in ls)
        read(owner,key,address,len(body));offset=address-windows[owner,key]['address'];memory[owner,key][offset:offset+len(body)]=body
    events=sorted([*tx['target_events'],*tx['source_events'],*tx['local_events'],*tx['local_memory_events']],key=lambda e:e['order'])
    assert [e['order'] for e in events]==list(range(len(events)))
    targets=[]
    for e in events:
        if 'conditions' in e:
            for c in e['conditions']:
                value=int.from_bytes(read(c['owner'],c['key'],c['address'],8),'little')
                assert value==c['observed_value'] and c['matched']
                assert value>=c['value'] if c['compare']=='ge' else value==c['value']
                assert 'reset_to' not in c
            continue
        if 'source' in e:
            i=e['operation'];node=nodes[i];s,t=e['source'],e['target'];n=s['bytes']
            assert node['rank']==s['owner']==t['owner'] and node['copy']==dict(source={k:s[k] for k in ('key','address','bytes')},target={k:t[k] for k in ('key','address')})
            times=local[i]
            if e['kind']=='acquired':
                assert i not in local_leases and e['time_ns']==times['ready_ns'];local_leases[i]=[s,t]
                assert all(not overlap(t,l) for l in source_leases.values())
            elif e['kind']=='captured':
                assert e['time_ns']==times['start_ns'] and bytes.fromhex(e['hex'])==read(s['owner'],s['key'],s['address'],n)
                copies[i]=bytes.fromhex(e['hex'])
            elif e['kind']=='committed':
                assert e['time_ns']==times['completed_ns'];write(t['owner'],t['key'],t['address'],copies[i],exclude=i)
            elif e['kind']=='released':assert e['time_ns']==times['completed_ns'];local_leases.pop(i)
            else:raise AssertionError('unknown local memory event')
        elif 'operation' in e:
            i=e['operation'];node=nodes[i];times=remote[i];owner=conns[node['connection']]['src']
            location={k:e[k] for k in ('owner','key','address','bytes')}
            assert location==dict(owner=owner,**node['source'])
            if e['kind']=='acquired':assert e['time_ns']==times['ready_ns'];source_leases[i]=location
            elif e['kind']=='captured':
                assert i in source_leases and e['time_ns']==times['published_ns']
                body=bytes.fromhex(e['hex']);assert body==read(owner,e['key'],e['address'],e['bytes']);source_bytes[i]=body
            elif e['kind']=='released':assert e['time_ns']==times['completed_ns'];source_leases.pop(i)
            else:raise AssertionError('unknown source event')
        else:
            identity=e['connection'],e['sequence'];assert identity not in seen and not e['duplicate'];seen.add(identity)
            row=next(o for o in remote.values() if (o['connection'],o['sequence'])==identity);node=nodes[row['id']];c=conns[node['connection']]
            assert e['time_ns']==row['applied_ns'] and e['destination_nic']==c['destination_nic']
            body=node['value'].to_bytes(8,'big') if node['verb']=='fetch_add' else source_bytes[row['id']] if 'source' in node else bytes.fromhex(node['hex'])
            assert (e['verb'],e['key'],e['address'],e['body_hex'])==(2 if node['verb']=='fetch_add' else 1,node['key'],node['address'],body.hex())
            if node['verb']=='fetch_add':
                old=int.from_bytes(read(c['dst'],node['key'],node['address'],8),'little')
                assert old==e['old_value']==row['returned_value'];body=((old+node['value'])%2**64).to_bytes(8,'little')
            write(c['dst'],node['key'],node['address'],body)
            targets.append(dict(operation=row['id'],bytes=len(body)))
    assert len(seen)==len(remote) and not source_leases and not local_leases
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    for w in tx['windows']:
        reconstructed=bytearray(w['bytes'])
        for page in w['pages']:body=bytes.fromhex(page['hex']);reconstructed[page['offset']:page['offset']+len(body)]=body
        assert reconstructed==memory[w['owner'],w['key']]
    return dict(targets=targets,source_bytes=source_bytes,copy_bytes=copies,memory=memory)


def wire_audit(result,initial,nodes):
    tx=result['transactions'];net=tx['transport'];capture=result['packet_capture'];assert capture['complete'] and not capture['omitted_events']
    headers=capture['wire_headers'];events=[];conns={c['id']:c for c in initial['connections']}
    remote={i:c for i,c in conns.items() if c['src']!=c['dst']};selfids=set(conns)-set(remote)
    for row,index in zip(capture['events'],headers['event_header_indices']):
        event=dict(zip(capture['columns'],row));header=dict(zip(headers['columns'],headers['headers'][index]))
        assert event['packet_uid']==header['packet_uid'] and header['source_port']==header['destination_port'];event['wire_port']=header['source_port'];events.append(event)
    assert len(events)==len(capture['events']);assert {s['id'] for s in net['streams']}=={2*i+j for i in remote for j in (0,1)}
    source=net['source_packets'];payload=net['source_payload_packets'];assert len(source)==len(payload)
    bytes_by_stream=defaultdict(dict);duplicate=0;starts=set()
    for row,p in zip(source,payload):
        time,sid,src,dst,nic,seq,size,uid=row;c=remote[sid//2]
        assert (src,dst,nic)==((c['dst'],c['src'],c['destination_nic']) if sid%2 else (c['src'],c['dst'],c['source_nic']))
        body=bytes.fromhex(p['hex']);assert (time,sid,seq,size)==(p['time_ns'],p['stream'],p['sequence'],len(body))
        duplicate+=(sid,seq) in starts;starts.add((sid,seq))
        for i,b in enumerate(body):assert bytes_by_stream[sid].get(seq+i,b)==b;bytes_by_stream[sid][seq+i]=b
    frames={};out_of_order=0
    assert initial['transport_recovery']['mode']=='go_back_n/1','this oracle models the declared in-order receiver only'
    for stream in net['streams']:
        sid=stream['id'];c=remote[sid//2];src,dst=(c['dst'],c['src']) if sid%2 else (c['src'],c['dst'])
        sn,dn=(c['destination_nic'],c['source_nic']) if sid%2 else (c['source_nic'],c['destination_nic']);port=c['response_port'] if sid%2 else c['request_port']
        assert (stream['src'],stream['dst'],stream['source_nic'],stream['destination_nic'],stream['wire_port'])==(src,dst,sn,dn,port)
        assert stream['qp_creations']==1 and stream['snd_una']==stream['snd_nxt']==stream['submitted_bytes']==stream['published_bytes']
        packets=[p for p in source if p[1]==sid]
        sent=[e for e in events if e['node']==src and e['wire_port']==port and e['event']==0 and e['ip_protocol']==17]
        assert Counter((p[0],p[7],p[5],p[6]) for p in packets)==Counter((e['time_ns'],e['packet_uid'],e['sequence'],e['data_payload_bytes']) for e in sent)
        assert all(e['port']==sn for e in sent)
        arrivals={};prefix=0
        for e in events:
            if (e['node'],e['wire_port'],e['event'],e['ip_protocol'])!=(dst,port,1,17):continue
            assert e['port']==dn and any(p[7]==e['packet_uid'] and (p[5],p[6])==(e['sequence'],e['data_payload_bytes']) for p in packets)
            # A packet arriving beyond the missing prefix is discarded by this
            # declared go-back-N receiver, not retained for later gap filling.
            if e['sequence']>prefix:out_of_order+=1;continue
            if e['sequence']<prefix:continue
            for offset in range(prefix,prefix+e['data_payload_bytes']):arrivals[offset]=e['time_ns']
            prefix+=e['data_payload_bytes']
        data=bytes_by_stream[sid];assert set(data)==set(arrivals)==set(range(stream['submitted_bytes']))
        raw=bytes(data[i] for i in range(len(data)));offset=0
        while offset<len(raw):
            magic,verb,sequence,address,key,size=struct.unpack_from('>IIQQII',raw,offset);assert magic==0x4d525431 and verb in (1,2,3)
            end=offset+32+size;body=raw[offset+32:end];assert len(body)==size
            work=next(w for w in net['work'] if (w['stream'],w['begin'],w['end'])==(sid,offset,end))
            assert work['accepted_ns']==max(arrivals[i] for i in range(end))
            acknowledgements=[e for e in events if (e['node'],e['wire_port'],e['event'],e['ip_protocol'])==(src,port,1,252) and e['sequence']>=end]
            assert acknowledgements and work['acked_ns']==min(e['time_ns'] for e in acknowledgements)
            assert all(e['port']==sn for e in acknowledgements)
            frames[sid,sequence]=dict(verb=verb,address=address,key=key,body=body,work=work);offset=end
    for row in tx['operations']:
        node=nodes[row['id']];c=conns[row['connection']]
        if row['connection'] in selfids:
            assert row['delivery']=='declared_self_service/1' and row['request_acked_ns']==row['response_ns']==-1
            continue
        frame=frames[2*row['connection'],row['sequence']]
        body=node['value'].to_bytes(8,'big') if node['verb']=='fetch_add' else next(bytes.fromhex(e['hex']) for e in tx['source_events'] if e['operation']==row['id'] and e['kind']=='captured') if 'source' in node else bytes.fromhex(node['hex'])
        assert (frame['verb'],frame['key'],frame['address'],frame['body'])==(2 if node['verb']=='fetch_add' else 1,node['key'],node['address'],body)
        assert (frame['work']['published_ns'],frame['work']['accepted_ns'],frame['work']['acked_ns'])==(row['published_ns'],row['applied_ns'],row['request_acked_ns'])
        if node['verb']=='fetch_add':
            reply=frames[2*row['connection']+1,row['sequence']]
            assert reply['verb']==3 and int.from_bytes(reply['body'],'big')==row['returned_value'] and reply['work']['accepted_ns']==row['response_ns']
    for cid in selfids:
        c=conns[cid];last=0
        for row in sorted((o for o in tx['operations'] if o['connection']==cid),key=lambda o:o['sequence']):
            begin=max(last,row['published_ns']);assert row['self_started_ns']==begin
            assert row['applied_ns']==begin+c['self_service']['apply_ns']
            assert row['completed_ns']==row['self_completed_ns']==begin+c['self_service']['complete_ns'];last=row['completed_ns']
    assert net['physical_drained'] and net['inflight_packets']==0 and net['closed']
    return dict(actual_source_packets=len(source),retransmitted_source_packets=duplicate,decoded_frames=len(frames),
        observed_out_of_order_data_packets=out_of_order,physical_capture_events=len(events))


def qp_context(sm,warp,sms,contexts,warps,notify):
    if contexts==1:return 0
    first=int(notify)
    candidates=list(range(first+sm,contexts,sms))
    return candidates[warp%len(candidates)] if sms<=contexts-first else first+(sm*warps+warp)%(contexts-first)


def audit(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    c=read('input.json');config=c['modern'];x=config['execution'];ranks=x['ranks'];cap=x['max_tokens_per_rank'];experts=config['model']['experts'];local=experts//ranks
    result=read('result.json');modern=read('modern-execution.json');initial=read('initial.json');plan=read('modern-plan.json')
    assert result['passed'] and result['session']['finished'] and result['drained'] and modern['runtime']['all_events_fired']
    nodes={n['id']:n for n in modern['native_operations']};tasks={int(i):t for i,t in modern['native_tasks'].items()}
    callbacks={(e['operation'],e['event']):e['time_ns'] for e in result['session']['events']};assert len(callbacks)==len(result['session']['events'])
    log=modern['events'];events={e['id']:e for e in plan['events']};ops={o['id']:o for o in plan['operations']};fired={};byoperation=defaultdict(dict)
    assert len(log)==len(events) and len(nodes)==result['completed_operations']+result['completed_local_work']
    direct=[]
    for row in (loadline for loadline in map(__import__('json').loads,(root/'control.jsonl').read_text().splitlines())):
        if row['direction']=='frontend':direct.extend(row['message']['submissions'])
    assert direct==modern['native_operations']
    group_next=Counter();slots={}
    for row in log:
        name=row['event'];event=events[name];assert name not in fired and all(d in fired and fired[d]['time_ns']<=row['time_ns'] for d in event['deps'])
        assert row['kind']==event['kind']
        if row['origin']=='native_callback':
            key=row['native_operation'],row['native_event'];assert key in callbacks and callbacks[key]==row['native_time_ns']<=row['time_ns']
            task=tasks[key[0]]
            if 'operation' in event:
                assert task['modern_operation']==event['operation'];byoperation[event['operation']][event['kind']]=row
                native=nodes[key[0]];localnode=native['kind']=='local'
                expected=('local_started' if localnode else 'submitted') if event['kind']=='issued' else 'completed' if localnode or event['kind']=='local_complete' else 'applied'
                assert row['native_event']==expected
            else:assert task['event']==name and row['native_event']=='completed'
        else:
            assert row['origin']=='zero_cost_control' and event['kind'] not in ('issued','accepted','visible','local_complete','token_prepared','counts_ready','barrier_ready','expert_stage_done','local_expert_reduction_done')
        if event['kind']=='slot_allocate':
            group=tuple(event['allocation_group']);assert row['slot_value']==group_next[group];group_next[group]+=1;slots[name]=row['slot_value']
        fired[name]=row
    assert set(fired)==set(events) and set(byoperation)==set(ops) and all(len(v)==4 for v in byoperation.values())
    memory=memory_audit(result,initial,nodes);wire=wire_audit(result,initial,nodes)
    expected_names=set();pairs={};groups={}
    for ci,call in enumerate(config['calls']):
        counts=Counter();pair=Counter()
        for src,tokens in enumerate(call['tokens']):
            for token,choices in enumerate(tokens):
                counts.update((src,e) for e in choices)
                for dst in set(e//local for e in choices):
                    pair[src,dst]+=1;groups[ci,src,token,dst]=[i for i,e in enumerate(choices) if e//local==dst]
        pairs[ci]=pair
        for phase in ('dispatch','combine'):
            for boundary in ('entry','exit'):
                expected_names.update(f'm{ci}.{phase}.{boundary}.s{s}.d{d}' for s in range(ranks) for d in range(ranks))
        for src in range(ranks):
            for dst in range(ranks):
                expected_names.update((f'm{ci}.dispatch.rank_count.s{src}.d{dst}',f'm{ci}.dispatch.expert_count.s{src}.d{dst}'))
                op=ops[f'm{ci}.dispatch.rank_count.s{src}.d{dst}'];assert op['value']==-pair[src,dst]-1
                op=ops[f'm{ci}.dispatch.expert_count.s{src}.d{dst}'];assert op['values']==[-counts[src,e]-1 for e in range(dst*local,(dst+1)*local)]
    for ci,src,token,dst in groups:
        expected_names.update((f'm{ci}.dispatch.data.s{src}.t{token}.d{dst}',f'm{ci}.combine.data.s{dst}.d{src}.t{token}'))
    assert set(ops)==expected_names
    conns={c['id']:c for c in initial['connections']};bindings={b['transaction_connection']:b for b in modern['bindings']};physical={d['rank']:d['node'] for d in c['deployment']}
    for name,op in ops.items():
        row=byoperation[name]['issued'];node=nodes[row['native_operation']];src,dst,ci=op['src'],op['dst'],op['call']
        assert node['rank']==physical[src] if op['path']=='local' else conns[node['connection']]['src']==physical[src] and conns[node['connection']]['dst']==physical[dst]
        if op['kind']=='barrier_signal':expected_path='gin_self' if src==dst else 'rdma';expected_context=0;key=2;address=32*2**20+8*src
        elif op['kind']=='rank_count':expected_path='local' if src==dst else 'rdma';expected_context=0;key=1;address=4096+49216+8*src
        elif op['kind']=='expert_count_block':expected_path='gin_self' if src==dst else 'rdma';expected_context=0;key=1;address=4096+49216+8*(ranks+src*local)
        else:
            expected_path='local' if src==dst else 'rdma';key=1;h=config['model']['hidden_size'];k=config['model']['top_k']
            metadata=8*k+(4+4*k if op['phase']=='dispatch' else 0);nbytes=((2*h+((metadata+31)//32*32)+63)//64)*64
            assert nbytes==op['operand_bytes']
            if op['phase']=='dispatch':
                slot=slots[op['slot_variable']];address=4096+16*2**20+(src*cap+slot)*nbytes
                channel=op['token']%(x['num_sms']*x['dispatch_warps']);sm=channel%x['num_sms'];warp=channel//x['num_sms']
                expected_context=qp_context(sm,warp,x['num_sms'],x['call_contexts'],x['dispatch_warps'],True)
                marker_name=f"dispatch:{ci}:{src}:{op['token']}"
            else:
                positions=groups[ci,dst,op['token'],src];plane=src if ranks<=k else positions[0]
                address=4096+16*2**20+(plane*cap+op['token'])*nbytes
                index=sum(pairs[ci][s,src] for s in range(dst))+slots[op['binding']['slot_variable']]
                per=(sum(pairs[ci][s,src] for s in range(ranks))+x['num_sms']*x['combine_warps']-1)//(x['num_sms']*x['combine_warps'])
                sm=(index//per)%x['num_sms'];warp=(index//per)//x['num_sms']
                expected_context=qp_context(sm,warp,x['num_sms'],x['call_contexts'],x['combine_warps'],False)
                marker_name=name
            marker=hashlib.sha256(('modern-native-marker/1:'+marker_name).encode()).digest()+bytes(nbytes-32)
            if op['path']!='local':assert memory['source_bytes'][node['id']]==marker
            elif op['phase']=='dispatch':assert memory['copy_bytes'][node['id']]==marker
            else:
                reduction=name.replace('.combine.data.s','.combine.s')+'.reduced'
                helper=next(i for i,t in tasks.items() if t.get('event')==reduction)
                assert memory['copy_bytes'][helper]==marker and nodes[helper]['copy']['target']==dict(key=key,address=address)
        assert op['path']==expected_path and row['binding']['context_index']==expected_context
        if op['path']!='local':
            connection=conns[node['connection']];binding=bindings[node['connection']]
            assert (node['key'],node['address'])==(key,address)
            assert (binding['source_rank'],binding['peer_rank'],binding['context_index'])==(src,dst,expected_context)
            assert ('self_service' in connection)==(op['path']=='gin_self')
        elif 'copy' in node:assert node['copy']['target']==dict(key=key,address=address)
    assert not modern['runtime']['memory_state']['active_source_leases'] and modern['runtime']['memory_state']['nonzero_count_elements']==0
    assert len(modern['runtime']['memory_state']['completed_barriers'])==4*ranks*len(config['calls'])
    for signal in modern['runtime']['memory_state']['signals']:assert signal['value']==signal['shadow']==4*len(config['calls'])
    return dict(passed=True,modern_operations=len(ops),modern_events=len(events),native_remote_operations=result['completed_operations'],
        native_local_operations=result['completed_local_work'],paths=dict(Counter(o['path'] for o in ops.values())),
        calls=len(config['calls']),ranks=ranks,decisions=modern['decisions'],finish_ns=result['finish_ns'],
        actual_target_applications=len(memory['targets']),**wire,scope='functional byte/event acceptance, not numerical model or calibrated GPU/provider performance')


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    assert not a.output.exists();save(a.output,audit(a.run))


if __name__=='__main__':main()
