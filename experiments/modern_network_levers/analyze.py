"""Read saved public outputs; do not import frontend/runtime/fixture drivers."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import struct


def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(root,overrides=None):
    def read(name):return overrides[name] if overrides and name in overrides else load(root/name)
    cfg=read('input.json');protocol=read('protocol.json');result=read('result.json')
    plan=read('modern-plan.json');execution=read('modern-execution.json')
    assert result['passed'] and result['drained'] and result['session']['finished']
    assert execution['runtime']['all_events_fired'] and protocol['payload_bytes']==2000
    assert cfg['provider']['transport_recovery']['mode']=='selective_repeat/1'
    assert not result['fault_drops'] and not execution['runtime']['memory_state']['active_source_leases']
    events={e['id']:e for e in plan['events']};fired={};phase_marks=[]
    for row in execution['events']:
        event=events[row['event']];assert row['event'] not in fired and event['kind']==row['kind']
        assert all(dep in fired and fired[dep]<=row['time_ns'] for dep in event['deps'])
        fired[row['event']]=row['time_ns']
        if row['kind'] in ('kernel_begin','application_stage_complete','barrier_ready','expert_stage_done'):
            phase_marks.append(dict(event=row['event'],kind=row['kind'],time_ns=row['time_ns']))
    assert set(fired)==set(events)
    assert len(execution['runtime']['memory_state']['completed_barriers'])==16
    assert execution['runtime']['memory_state']['nonzero_count_elements']==0
    native={n['id']:n for n in execution['native_operations']}
    tx=result['transactions'];net=tx['transport'];connections={c['id']:c for c in load(root/'initial.json')['connections']}
    assert len({(e['connection'],e['sequence']) for e in tx['target_events']})==len(tx['target_events'])==len(tx['operations'])
    assert all(not e['duplicate'] for e in tx['target_events'])
    assert len(native)==result['completed_operations']+result['completed_local_work']
    assert not tx['active_source_leases'] and not tx['active_local_memory_leases']
    recovery=net['recovery'];assert recovery['complete'] and not recovery['omitted_events'] and not recovery['failed']
    assert all(s['prefix_complete'] and s['acknowledged_bytes']==s['published_bytes'] for s in recovery['states'])
    data=defaultdict(dict);duplicates=0;seen=set();source_bytes=0
    assert len(net['source_packets'])==len(net['source_payload_packets'])
    for packet,payload in zip(net['source_packets'],net['source_payload_packets']):
        now,sid,src,dst,nic,seq,nbytes,uid=packet;body=bytes.fromhex(payload['hex'])
        assert (now,sid,seq,nbytes)==(payload['time_ns'],payload['stream'],payload['sequence'],len(body))
        assert nbytes<=2000;source_bytes+=nbytes;duplicates+=(sid,seq) in seen;seen.add((sid,seq))
        for offset,b in enumerate(body):assert data[sid].get(seq+offset,b)==b;data[sid][seq+offset]=b
    frames={};ranges=defaultdict(list)
    for stream in net['streams']:
        sid=stream['id'];n=stream['submitted_bytes'];assert stream['snd_una']==stream['snd_nxt']==stream['published_bytes']==n
        assert set(data[sid])==set(range(n));body=bytes(data[sid][i] for i in range(n));offset=0
        while offset<n:
            magic,verb,seq,address,key,size=struct.unpack_from('>IIQQII',body,offset)
            assert magic==0x4d525431 and verb in (1,2,3) and offset+32+size<=n
            assert (sid,seq) not in frames
            frames[sid,seq]=(verb,address,key,body[offset+32:offset+32+size])
            ranges[sid].append((offset,offset+32+size,seq));offset+=32+size
    roles={int(k):v for k,v in execution['native_tasks'].items()};ops={o['id']:o for o in plan['operations']}
    byte_groups=Counter();operation_groups=Counter();waits=defaultdict(list);frame_groups={}
    for op in tx['operations']:
        node=native[op['id']];modern=ops[roles[op['id']]['modern_operation']]
        group='token_data' if modern['kind']=='token_data' else 'control'
        assert modern['kind'] in ('token_data','barrier_signal','rank_count','expert_count_block')
        operation_groups[group]+=1;waits[group].append(op['completed_ns']-op['ready_ns'])
        conn=connections[op['connection']]
        if conn['src']==conn['dst']:continue
        sid=2*op['connection'];verb,address,key,body=frames[sid,op['sequence']]
        frame_groups[sid,op['sequence']]=group
        expected=8 if node['verb']=='fetch_add' else modern['operand_bytes']
        assert (verb,address,key,len(body))==(2 if node['verb']=='fetch_add' else 1,node['address'],node['key'],expected)
        byte_groups[group+'_request_operand']+=len(body);byte_groups[group+'_request_frame']+=32+len(body)
        if node['verb']=='fetch_add':
            reply=frames[sid+1,op['sequence']];assert reply[0]==3 and int.from_bytes(reply[3],'big')==op['returned_value']
            frame_groups[sid+1,op['sequence']]='control'
            byte_groups['control_reply_frame']+=32+len(reply[3])
    assert set(frame_groups)==set(frames)
    packet_groups=Counter()
    for packet in net['source_packets']:
        _,sid,_,_,_,begin,nbytes,_=packet
        groups={frame_groups[sid,seq] for first,last,seq in ranges[sid] if first<begin+nbytes and begin<last}
        assert len(groups)==1,'mixed semantic packet requires separate attribution'
        packet_groups[next(iter(groups))]+=1
    assert source_bytes==sum(byte_groups[k] for k in ('control_request_frame','control_reply_frame','token_data_request_frame'))
    required=Counter()
    for src,tokens in enumerate(cfg['modern']['calls'][0]['tokens']):
        for token,experts in enumerate(tokens):
            for dst in {e//64 for e in experts}:required[src,dst]+=1
    expected_data=sum(n for (s,d),n in required.items() if s!=d)*2
    assert sum('.data.' in op['id'] and op['path']=='rdma' for op in ops.values())==expected_data
    initial=load(root/'initial.json');assert initial['dcqcn_reference']==cfg['provider']['dcqcn_reference']
    assert net['dcqcn_reference']['config']==initial['dcqcn_reference']
    ports=result['ports'];rates=sorted({p['rate_bps'] for p in ports});delays=sorted({p['delay_ns'] for p in ports})
    expected_rate=200000000000 if root.name=='bandwidth' else 100000000000
    expected_delay=500 if root.name=='propagation' else 1000
    assert rates==[expected_rate] and delays==[expected_delay]
    finish=max(fired.values());assert finish<=result['finish_ns']
    final_stages=[fired[e['id']] for e in events.values() if e['kind']=='application_stage_complete' and '.combine.' in e['id']]
    assert len(final_stages)==4
    stage_complete=max(final_stages)
    assert execution['runtime']['all_application_complete'] and stage_complete<=finish
    controls=[v for v in net['dcqcn_reference']['events'] if 'cnp' in v.get('kind','').lower()]
    return dict(passed=True,finish_ns=result['finish_ns'],last_modern_event_ns=finish,
        model_application_stage_complete_ns=stage_complete,
        full_modern_events_and_source_release_ns=finish,network_drained_ns=result['finish_ns'],
        modern_operations=len(ops),modern_events=len(events),native_operations=len(native),
        remote_token_data_operations=expected_data,remote_and_self_operation_groups=dict(operation_groups),
        remote_frame_and_operand_bytes=dict(byte_groups),source_payload_bytes=source_bytes,
        source_packets=len(net['source_packets']),repeated_source_packets=duplicates,
        source_packet_groups=dict(packet_groups),
        control_frame_byte_fraction=(byte_groups['control_request_frame']+byte_groups['control_reply_frame'])/source_bytes,
        control_source_packet_fraction=packet_groups['control']/len(net['source_packets']),
        timeout_expiries=sum(s['timeout_expiries'] for s in recovery['states']),
        reference_cnp_named_events=len(controls),reference_event_kinds=dict(Counter(e['kind'] for e in net['dcqcn_reference']['events'])),
        rates_bps=rates,link_delays_ns=delays,phase_marks=phase_marks,
        capture_complete=result['packet_capture']['complete'],omitted_capture_events=result['packet_capture']['omitted_events'],
        packet_path_reconstruction_claimed=False,
        operation_wait_scope='descriptive per-operation ready-to-complete intervals; not additive application losses',
        max_operation_wait_ns={k:max(v) for k,v in waits.items()},
        input_sha256=sha(root/'input.json'),plan_sha256=sha(root/'modern-plan.json'))


def analyze(stage):
    assert load(stage/'execution.json')['closed'];source=load(stage/'source.json')
    cfg=load(stage/'inputs/input.json')
    assert cfg['modern']['calls'][0]['tokens']==[row['choices'] for row in source['selected_rows']]
    cases={name:audit(stage/name) for name in ('baseline','bandwidth','propagation')}
    assert len({v['input_sha256'] for v in cases.values()})==1 and len({v['plan_sha256'] for v in cases.values()})==1
    assert len({json.dumps(v['remote_frame_and_operand_bytes'],sort_keys=True) for v in cases.values()})==1
    metrics=('model_application_stage_complete_ns','full_modern_events_and_source_release_ns','network_drained_ns')
    effects={metric:{name:dict(saved_ns=cases['baseline'][metric]-v[metric],
            relative_reduction=(cases['baseline'][metric]-v[metric])/cases['baseline'][metric])
            for name,v in cases.items() if name!='baseline'} for metric in metrics}
    return dict(passed=True,cases=cases,effects=effects,
        both_levers_improve=all(v['saved_ns']>0 for values in effects.values() for v in values.values()),
        larger_observed_lever={metric:max(values,key=lambda k:values[k]['saved_ns']) for metric,values in effects.items()},
        completion_scope='model application stage, all modern callbacks/source release, and physical drain are distinct; none is measured end-user latency',
        scope='one held-out routing step, four ranks, one layer, declared compute/provider costs; sensitivity, not calibrated speedup or production generalization')


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();save(a.output,analyze(a.stage))


if __name__=='__main__':main()
