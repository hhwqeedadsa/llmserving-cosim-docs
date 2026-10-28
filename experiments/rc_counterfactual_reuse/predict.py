"""A guarded terminal-ACK-loss prototype; reads observed data only."""
import argparse
from collections import deque
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct


def read(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


def path_to(topology,src,dst):
    # A connected tree has one physical route, including the NIC at either end.
    adjacency={node['id']:[] for node in topology['nodes']}
    assert len(topology['links'])==len(adjacency)-1,'requires unique tree paths'
    for link in topology['links']:
        for a,b,ap,bp in [(link['src'],link['dst'],link['src_port'],link['dst_port']),
                          (link['dst'],link['src'],link['dst_port'],link['src_port'])]:
            adjacency[a].append(dict(src=a,dst=b,src_port=ap,dst_port=bp,
                                    delay_ns=link['delay_ns'],rate_bps=link['rate_bps']))
    queue=deque([(src,[])]);seen={src}
    while queue:
        node,route=queue.popleft()
        if node==dst:return route
        for edge in adjacency[node]:
            if edge['dst'] not in seen:
                seen.add(edge['dst']);queue.append((edge['dst'],route+[edge]))
    raise AssertionError('unreachable RC endpoints')


def isolated(route,start,size,label):
    events=[];time=start
    for edge in route:
        # DataRate -> Seconds(int64x64_t) -> Time(v.Round()), at ns resolution.
        serial=(size*8*10**9+edge['rate_bps']//2)//edge['rate_bps']
        events.append(dict(kind='tx',node=edge['src'],port=edge['src_port'],time_ns=time,bytes=size,packet=label))
        time+=serial+edge['delay_ns']
        events.append(dict(kind='rx',node=edge['dst'],port=edge['dst_port'],time_ns=time,bytes=size,packet=label))
    return events,time


def predict(observed,case):
    base_work=read(observed/'healthy.json');work=read(observed/(case+'.json'))
    result=read(observed/'healthy-run/result.json');topology=read(observed/'topology/topology.json')
    protocol=read(observed/'rdma.json');base=result['rc_concurrent'];rule=work['loss']
    recorded={Path(path).name:value for path,value in read(observed/'healthy-run/inputs.json')['hashes'].items()}
    assert recorded=={'topology.json':sha(observed/'topology/topology.json'),
        'rdma.json':sha(observed/'rdma.json'),'healthy.json':sha(observed/'healthy.json'),
        'moe-rdma-rc-concurrent':read(observed/'identity.json')['backend_sha256']},'cache/input identity mismatch'
    unchanged=deepcopy(work);unchanged['loss']=None
    if unchanged!=base_work:return dict(admitted=False,reason='operation_program_changed')
    if not rule or rule['opcode']!=18:return dict(admitted=False,reason='not_terminal_atomic_ack_loss')
    q=next(q for q in work['connections'] if q['id']==rule['connection'])
    count=sum((len(bytes.fromhex(op['data_hex']))+work['mtu']-1)//work['mtu']
              if op['kind']=='write' else 1 for op in q['operations'])
    if q['operations'][-1]['kind']!='fetch_add' or rule['psn']!=count-1 or rule['node']!=q['src']:
        return dict(admitted=False,reason='not_final_operation_response')
    assert result['passed'] and result['drained'] and base['drained']
    assert base_work['loss'] is None and base['drops']==0
    assert all(q['timeouts']==0 for q in base['connections'])
    assert result['packet_capture']['complete'] and result['packet_capture']['omitted_events']==0
    if any(p['pfc_pause'] or p['pfc_resume'] or p['ecn_ce_seen'] for p in result['ports']):
        return dict(admitted=False,reason='observed_feedback_or_pause_state')
    assert not set(protocol).intersection(('faults','drop_once','recovery','admission','link_failures','local_failover'))
    def selected(e,kind,opcode):
        body=bytes.fromhex(e['body_hex'])
        return e['connection']==q['id'] and e['kind']==kind and body[0]==opcode and \
               struct.unpack_from('!I',body,8)[0]&0xffffff==rule['psn']
    requests=[e for e in base['endpoint_events'] if selected(e,'request_sent',20)]
    responses=[e for e in base['endpoint_events'] if selected(e,'response_received',18)]
    assert len(requests)==len(responses)==1
    retry=requests[0]['time_ns']+work['rto_ns']
    if retry<=result['finish_ns']:return dict(admitted=False,reason='retry_overlaps_baseline_network')
    forward=path_to(topology,q['src'],q['dst']);reverse=path_to(topology,q['dst'],q['src'])
    assert forward[0]['src_port']==q['source_nic'] and forward[-1]['dst_port']==q['destination_nic']
    # Packet IDs are internal evidence. All on-wire protocol fields are reused.
    uids=[requests[0]['packet_uid'],responses[0]['packet_uid']];sizes=[]
    for uid in uids:
        wires=[e['packet_hex'] for e in base['physical_packets'] if e['packet_uid']==uid]
        assert wires and len(set(map(len,wires)))==1
        sizes.append(len(bytes.fromhex(wires[0])))
    # No observed CE is insufficient when ECN marking is probabilistic. Bound
    # the isolated tail below every configured marking threshold explicitly.
    if any(max(sizes)>=row['kmin_bytes'] for row in protocol['ecn_by_rate'].values()):
        return dict(admitted=False,reason='tail_may_cross_ecn_threshold')
    if protocol['switch_buffer_bytes']!=8388608:
        return dict(admitted=False,reason='unreviewed_mmu_configuration')
    request_events,arrived=isolated(forward,retry,sizes[0],'replayed_atomic_request')
    reply_events,completed=isolated(reverse,arrived,sizes[1],'cached_atomic_ack')
    states={s['id']:deepcopy(s) for s in base['connections']}
    states[q['id']]['operations'][-1]['completed_ns']=completed
    states[q['id']]['timeouts']=1
    gpu_ids={r['id'] for r in topology['nodes'] if r['kind']=='gpu'}
    source_counts=dict(request=0,response=0)
    for event in base['physical_packets']:
        if event['kind']=='tx' and event['node'] in gpu_ids:
            opcode=bytes.fromhex(event['packet_hex'])[30]
            source_counts['response' if opcode in (17,18) else 'request']+=1
    tx=sum(e['kind']=='tx' for e in base['physical_packets'])
    return dict(admitted=True,reason='terminal_feedback_loss_and_idle_retry',connection=q['id'],
        lost_uid=responses[0]['packet_uid'],lost_receive_ns=responses[0]['time_ns'],retry_ns=retry,
        baseline_finish_ns=result['finish_ns'],finish_ns=completed,request_arrival_ns=arrived,
        blind_old_rtt_finish_ns=responses[0]['time_ns']+work['rto_ns'],
        ordinary_idle_path_finish_ns=completed,ordinary_analytic_equals_reuse=True,
        connections=[states[k] for k in sorted(states)],memories=base['memories'],
        target_effects=base['target_effects'],source_packets={k:v+1 for k,v in source_counts.items()},
        physical_tx=tx+len(forward)+len(reverse),physical_rx=tx-1+len(forward)+len(reverse),physical_drop=1,
        retained_baseline_tx_records=tx,new_tail_tx_records=len(forward)+len(reverse),
        tail_events=request_events+reply_events,
        assertion_scope='terminal ACK loss only; fixed rate, no feedback, unique route and isolated retry',
        performance_claim=False)


def execute(stage,output):
    assert not output.exists()
    observed=stage/'observed'
    inputs={str(p.relative_to(observed)):sha(p) for p in sorted(observed.rglob('*')) if p.is_file()}
    cases={name:predict(observed,name) for name in ('ack_loss','data_loss')}
    assert cases['ack_loss']['admitted'] and not cases['data_loss']['admitted']
    save(output,dict(passed=True,cases=cases,observed_hashes=inputs,
        truth_read=False,stage_scope='developmental validation, not hardware or independent holdout'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    execute(args.stage.resolve(),args.output.resolve())
