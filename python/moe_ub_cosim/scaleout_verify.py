"""Independent conservation and execution-causality audit of a native service run."""
from collections import Counter
from pathlib import Path
import re

from .scaleout_rdma import checked_json
from .scaleout_topology import require
from .scaleout_limits import limits


def node_events(directory):
    """Read lossless node evidence, or the legacy logs for historical runs.

    Never merge both: node events occur in both sinks. A present but empty or
    truncated dedicated file must fail the subsequent completeness checks.
    """
    directory=Path(directory)
    dedicated=directory/'log'/'astra_nodes.log'
    paths=[dedicated] if dedicated.is_file() else sorted((directory/'log').glob('log*'))
    pattern=re.compile(r'(issue|callback),sys->id=(\d+), tick=(\d+), node->id=(\d+), node->name=.*?, node->type=\d+, graph_id=([^\s,]+)')
    events={}
    for path in paths:
        with path.open() as stream:
            for line in stream:
                match=pattern.search(line)
                if match:
                    kind,rank,tick,node,graph=match.groups(); key=graph,int(rank),int(node),kind
                    require(key not in events, 'duplicate ASTRA node event')
                    events[key]=int(tick)
    return events


def event_time(events, graph, rank, node, kind):
    key=graph,rank,node,kind
    require(key in events, 'missing ASTRA node evidence '+str(key)+
            '; inspect log completeness (legacy rotating logs can discard early events)')
    return events[key]


def verify(directory):
    directory=Path(directory).resolve()
    rdma=checked_json(directory/'rdma.json',65536)
    result=checked_json(directory/'result.json',limits(rdma)['result_json_bytes'])
    frontend=checked_json(directory/'frontend.json',4194304)
    require(result['passed'] and result['drained'] and result['service']['service_finished'] and
            result['service']['active_graphs']==0, 'service did not drain')
    from .scaleout_admission import audit
    admission_resources=audit(result)
    from .scaleout_metrics import phase_intervals, network_summary
    network_queues=network_summary(result)
    for flow in result['messages']:phase_intervals(flow,result['finish_ns'])
    libraries=[Path(x).resolve() for x in result['loaded_libraries'] if Path(x).name.startswith('libns3')]
    require(len(libraries)>=5 and len({p.parent for p in libraries})==1 and
            all(p.name.startswith('libns3.42-') and 'unified' not in p.name for p in libraries),
            'service loaded mixed or UB ns-3 libraries')
    events=node_events(directory)
    flows={m['id']:m for m in result['messages']}
    callbacks={m['id']:m for m in result['service']['message_callbacks']}
    require(len(flows)==len(result['messages'])==len(callbacks), 'duplicate or missing message results')
    descriptors={};node_count=0;total_bytes=0
    config=checked_json(directory/'input.json',1048576)
    replay=None
    if 'route_replay' in config:
        from .scaleout_replay import Replay
        replay=Replay(config,directory)
    topology=checked_json(directory/'topology.json',8388608)
    physical={n['id']:n for n in topology['nodes']}
    ports={(p['node'],p['port']):p for p in result['ports']}
    for port in result['ports']:
        if physical[port['node']]['kind']!='gpu':
            threshold=rdma['ecn_by_rate'][str(port['rate_bps'])]
            require(port['ecn_kmin_bytes']==threshold['kmin_bytes'] and port['ecn_kmax_bytes']==threshold['kmax_bytes'],
                    'effective MMU ECN thresholds differ from byte-valued input')
    h,bpe=config['model']['hidden_size'],config['model']['activation_bytes']
    local_payload=Counter()
    for history in frontend['history']:
        graph=history['graph_id']; wave=checked_json(directory/'graphs'/graph/'wave.json',min(limits(rdma)['result_json_bytes'],268435456))
        nodes=wave['rank_nodes']; ranks=wave['rank_ids']
        if replay:
            require(wave.get('route_source')==replay.manifest['source']['kind'], 'wave source kind mismatch')
        require(ranks==config['parallel']['groups'][history['group_index']] and
                wave['decode_step']==history['step'], 'wave changed EP group or Decode step')
        require(len(wave['routes'])==config['model']['layers'] and
                [l['layer'] for l in wave['routes']]==list(range(config['model']['layers'])),
                'missing/duplicate wave route layers')
        # Recompute the matrix from individual token choices, independently of
        # the generator's preaggregated contribution table.
        expected={}
        for layer in wave['routes']:
            matrix=Counter()
            seen_tokens=set()
            for token in layer['tokens']:
                key=token['rank'],token['slot']
                require(key not in seen_tokens and token['rank'] in ranks and
                        0<=token['slot']<config['workload']['batch_per_rank'], 'invalid/repeated token source')
                seen_tokens.add(key)
                require(len(token['experts'])==len(set(token['experts']))==config['model']['top_k'], 'invalid Top-K')
                if replay:
                    request_id=token['rank']*config['workload']['batch_per_rank']+token['slot']
                    record=replay.records[request_id]
                    offset=(history['step']*config['model']['layers']+layer['layer'])*config['model']['top_k']
                    require(token['experts']==list(record['choices'][offset:offset+config['model']['top_k']]),
                            'wave choices differ from hash-checked source')
                    require(layer.get('source_layer_id')==replay.layers[layer['layer']] and token.get('source')==dict(
                        request_id=request_id,file_id=record['file_id'],source_entry=record['first_decode_entry']+history['step'],
                        source_token_row=0,source_layer_id=replay.layers[layer['layer']],sha256=record['source_file']['sha256']),
                        'wave source identity/index mismatch')
                for expert in token['experts']:
                    require(0<=expert<config['model']['experts'], 'expert outside model')
                    placement=config.get('route_replay',{}).get('expert_to_group_rank')
                    owner=placement[layer['layer']][expert] if placement is not None else expert//(config['model']['experts']//len(ranks))
                    dst=ranks[owner]
                    if token['rank']!=dst:
                        matrix[token['rank'],dst]+=h*bpe
            require(seen_tokens=={(r,s) for r in ranks for s in range(config['workload']['batch_per_rank'])},
                    'missing scheduled token routes')
            for (src,dst),size in matrix.items():
                expected[layer['layer'],'dispatch',src,dst]=size
                expected[layer['layer'],'combine',dst,src]=size
        actual={(m['layer'],m['phase'],m['src'],m['dst']):m['bytes'] for m in wave['messages']}
        require(actual==expected and len(actual)==len(wave['messages']), 'routing to remote payload mismatch')
        for m in wave['messages']:
            identity=m['id']; require(identity not in descriptors, 'message identity reused across waves')
            descriptors[identity]=m; flow=flows[identity]; cb=callbacks[identity]
            require(flow['src']==m['src'] and flow['dst']==m['dst'] and flow['plane']==m['plane'] and
                    flow['bytes']==m['bytes'] and flow['ack_callbacks']==1,
                    'message payload, path plane or completion mismatch')
            if rdma.get('faults') or 'drop_once' in rdma:
                require(flow.get('source_unique_data_payload_bytes')==m['bytes'] and
                        flow.get('receiver_contiguous_bytes')==m['bytes'] and
                        flow['source_data_payload_bytes']>=m['bytes'],
                        'fault run must conserve unique source and accepted receiver bytes; retries counted separately')
            else:
                require(flow['source_data_payload_bytes']==m['bytes'], 'unexpected source retransmission in loss-free run')
            require(history['start_ns']<=flow['ready_ns']<=flow['first_tx_ns']<=flow['receive_ready_ns']<=flow['ack_ns']<=history['finished_ns'],
                    'network message time order violated')
            if 'admission_granted_ns' in flow:
                require(flow['ready_ns']<=flow['admission_granted_ns']==flow['qp_registered_ns']<=flow['first_tx_ns'] and
                        flow['ack_ns']<=flow['sender_resource_released_ns']<=history['finished_ns'] and
                        flow['sender_qp_present'] is False, 'sender resource/admission boundary mismatch')
            require(cb['send_post_ns']==flow['ready_ns'] and
                    cb['send_callback_ns']==flow.get('sender_resource_released_ns',flow['ack_ns']) and
                    cb['recv_callback_ns']==max(cb['recv_post_ns'],flow['receive_ready_ns']), 'ASTRA message callback boundary mismatch')
            is_local=(topology.get('intra_host',{}).get('enabled',False) and
                      physical[m['src']]['server']==physical[m['dst']]['server'])
            require((flow.get('transport')=='intra_host_chunked_crossbar')==is_local,'intra/inter-host transport mismatch')
            if is_local:
                require(not flow['first_packet_path'] and flow['ack_ns']==flow['receive_ready_ns'],
                        'intra-host model must not emit RDMA packets/ACK')
                setting=topology['intra_host'];chunk=flow['intra_first_chunk']
                duration=(min(m['bytes'],setting['chunk_bytes'])*8000000000+setting['rate_bps']-1)//setting['rate_bps']
                require(chunk['ingress_start_ns']>=flow['ready_ns'] and
                        chunk['ingress_end_ns']-chunk['ingress_start_ns']==duration and
                        chunk['egress_start_ns']>=chunk['ingress_end_ns']+setting['delay_ns'] and
                        chunk['egress_end_ns']-chunk['egress_start_ns']==duration and
                        flow['receive_ready_ns']>=chunk['egress_end_ns']+setting['delay_ns'],
                        'intra-host serialization/propagation constraint violated')
                require(flow['intra_host_chunks']==(m['bytes']+setting['chunk_bytes']-1)//setting['chunk_bytes'],
                        'local chunk count mismatch')
                local_payload[m['src'],'to_switch']+=m['bytes']
                local_payload[m['dst'],'from_switch']+=m['bytes']
            else:
                current=m['src']
                for hop in flow['first_packet_path']:
                    require(hop['node']==current, 'packet path is not a contiguous physical route')
                    if physical[current]['kind']!='gpu':
                        require(physical[current]['plane']==m['plane'], 'packet crossed planes')
                    current=ports[current,hop['port']]['peer']
                first_attempt_dropped=any(e[2]==current and e[4]==flow.get('first_packet_uid')
                    for e in result.get('fault_injection',{}).get('events',[]))
                require(current==m['dst'] or first_attempt_dropped,
                        'first packet path neither reached destination nor ended at its injected drop')
            total_bytes+=m['bytes']
        for rank,tasks in nodes.items():
            rank=int(rank);by_id={n['id']:n for n in tasks}
            require(len(by_id)==len(tasks), 'duplicate DAG node ID')
            for task in tasks:
                start=event_time(events,graph,rank,task['id'],'issue')
                end=event_time(events,graph,rank,task['id'],'callback')
                require(history['start_ns']<=start<=end<=history['finished_ns'], 'node escaped wave bounds')
                for dep in task['deps']:
                    require(dep in by_id and start>=events[graph,rank,dep,'callback'], 'ASTRA consumer ran before predecessor')
                if task['kind']=='COMP':
                    require(end-start==task['duration_ns'], 'declared compute duration did not execute')
                else:
                    cb=callbacks[task['message']['id']];prefix='send' if task['kind']=='SEND' else 'recv'
                    require(start==cb[prefix+'_post_ns'] and end==cb[prefix+'_callback_ns'], 'graph/adapter callback mismatch')
                node_count+=1
    require(set(descriptors)==set(flows), 'extra network work outside frontend graphs')
    actual_local={(p['gpu'],p['direction']):p['payload_bytes'] for p in result.get('intra_host_links',[])}
    require(dict(local_payload)==actual_local,'intra-host physical link payload does not conserve messages')
    # Actual next-wave start must equal the preceding wave's callback time.
    for group_index in range(len(config['parallel']['groups'])):
        waves=sorted((w for w in frontend['history'] if w['group_index']==group_index),key=lambda w:w['step'])
        require(len(waves)==config['workload']['decode_steps'], 'missing Decode waves')
        require(waves[0]['start_ns']==0 and all(b['start_ns']==a['finished_ns'] for a,b in zip(waves,waves[1:])),
                'next batch did not follow the real preceding completion')
    require(all(q['state']==('OBSERVATION_ENDED' if replay else 'FINISHED') and
                (not replay or q['scheduler_state']=='FINISHED') and
                len(q['intertoken_ns'])==config['workload']['decode_steps'] for q in frontend['requests']),
            'request did not complete every Decode step')
    if replay:
        require({q['id'] for q in frontend['requests']}==set(replay.records) and
                len(frontend['requests'])==len(replay.records), 'missing/duplicate replay request')
        require(all(q['initial_context_tokens']==replay.records[q['id']]['context_tokens']
                    for q in frontend['requests']), 'replay initial context mismatch')
    report=dict(passed=True,messages=len(flows),remote_payload_bytes=total_bytes,verified_nodes=node_count,
                waves=len(frontend['history']),finish_ns=result['finish_ns'],
                evidence='raw token routes, native messages/MMU thresholds/paths, ASTRA issue/callback log, frontend completion history')
    if admission_resources['available']:
        report['admission_resources']=admission_resources
    if network_queues['available']:report['network_queues']=network_queues
    if 'intra_host' in topology:
        report.update(intra_host_payload_bytes=sum(local_payload.values())//2,
                      inter_host_payload_bytes=total_bytes-sum(local_payload.values())//2)
    if replay:
        report.update(route_source=replay.manifest['source']['kind'],source_records_verified=sum(
            len(g)*config['workload']['batch_per_rank']*config['model']['layers']*config['workload']['decode_steps']
            for g in config['parallel']['groups']),eos_inferred=False)
    return report
