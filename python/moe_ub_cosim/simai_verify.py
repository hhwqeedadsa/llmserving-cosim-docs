"""Source-to-DAG-to-packet completion audit for sparse SimAI services."""
from collections import Counter
import json
from pathlib import Path

from .scaleout_limits import limits
from .scaleout_rdma import checked_json
from .scaleout_topology import require
from .scaleout_verify import node_events, event_time
from .simai_frontend import verify_runtime
from .simai_service import validate


def verify(directory):
    directory=Path(directory).resolve();c=checked_json(directory/'input.json',1048576)
    t=checked_json(directory/'topology.json',8388608);p=checked_json(directory/'rdma.json',65536)
    n,planes,replay=validate(c,t,p,directory);receipt=verify_runtime(directory/'simai_runtime')
    r=checked_json(directory/'result.json',limits(p)['result_json_bytes']);f=checked_json(directory/'frontend.json',4*2**20)
    if 'service_transport' in r:
        from .service_stream_verify import verify as verify_streams
        return verify_streams(directory)
    require(r['native_network_executed'] and r['passed'] and r['drained'] and r['service']['service_finished'] and r['service']['active_graphs']==0,
            'SimAI native fabric did not finish and drain')
    require(r['gpu_count']==n and f['scheduler_source']=='SimAI_Vidur_real_Orca_Cluster_Batch_Request' and
            f['simai_revision']==receipt['revision'],'frontend/native identity mismatch')
    libs=[Path(x) for x in r['loaded_libraries'] if Path(x).name.startswith('libns3')]
    require(len(libs)>=5 and len({x.parent for x in libs})==1 and
            all(x.name.startswith('libns3.42-') and 'unified' not in x.name for x in libs),'mixed or UB native libraries')
    groups=c['service']['parallel']['groups'];w=c['service']['workload'];m=c['service']['model'];cost=c['service']['compute']
    require(f['cluster_gpu_counts']==[len(g) for g in groups] and f['time_origin_seconds']==1.0,
            'SimAI physical cluster or time origin differs')
    expected_sources=sorted({record['rank_id'] for record in replay.records.values()})
    require(f['active_source_ranks']==expected_sources and
            f['final_allocated_blocks']=={str(rank):0 for rank in expected_sources},'SimAI source ranks or final KV blocks differ')
    require(f['scheduler_calls']==len(expected_sources)*w['decode_steps'],'SimAI scheduler call count differs')
    requests={q['id']:q for q in f['requests']}
    require(set(requests)==set(replay.records) and len(requests)==len(f['requests']),'missing/duplicate SimAI request')
    for identity,record in replay.records.items():
        q=requests[identity]
        require(q['state']=='OBSERVATION_ENDED' and q['file_id']==record['file_id'] and
                q['context_tokens']==record['context_tokens'] and
                q['initial_processed_tokens']==record['context_tokens']+1 and
                q['num_processed_tokens']==record['context_tokens']+1+w['decode_steps'],
                'SimAI request did not execute its exact observation window')
    raw={i:json.loads(record['path'].read_bytes()) for i,record in replay.records.items()}
    native_waves={v['graph_id']:v for v in r['service']['waves']};flows={v['id']:v for v in r['messages']}
    callbacks={v['id']:v for v in r['service']['message_callbacks']}
    require(len(flows)==len(r['messages'])==len(callbacks),'duplicate/missing native message result')
    require(len(native_waves)==len(f['history'])==len(groups)*w['decode_steps'],'missing native wave')
    events=node_events(directory);descriptors={};nodes_checked=0;source_rows=0;total_bytes=0
    topology_nodes={node['id']:node for node in t['nodes']};ports={(row['node'],row['port']):row for row in r['ports']}
    for history in f['history']:
        graph=history['graph_id'];group=groups[history['group_index']];step=history['step']
        native=native_waves[graph]
        require(all(history[k]==native[k] for k in ('graph_id','start_ns','finished_ns')) and native['rank_ids']==group,
                'frontend/native graph completion differs')
        chosen={i for i,record in replay.records.items() if record['rank_id'] in group}
        require(history['selected_request_ids']==sorted(chosen),'SimAI did not select exact real request set')
        expected_batches={str(rank):sorted(i for i in chosen if replay.records[i]['rank_id']==rank) for rank in group if rank in expected_sources}
        require({k:sorted(v) for k,v in history['batch_request_ids'].items()}==expected_batches,'SimAI per-rank batch differs')
        wave=checked_json(directory/'graphs'/graph/'wave.json',16*2**20)
        require(wave['rank_ids']==group and wave['decode_step']==step and wave['group_index']==history['group_index'] and
                wave['route_source']==c['routes']['source']['kind'],'wave group/index/source differs')
        require([v['source_layer_id'] for v in wave['routes']]==c['routes']['layer_ids'],'source layers changed')
        expected={};source_tokens=Counter(replay.records[i]['rank_id'] for i in chosen);expert_work={}
        for local_layer,source_layer in enumerate(c['routes']['layer_ids']):
            matrix=Counter();received=Counter();tokens={}
            for i in sorted(chosen):
                record=replay.records[i];entry=record['first_decode_entry']+step
                choices=raw[i][entry][str(source_layer)][0]
                token=dict(rank=record['rank_id'],slot=record['slot'],experts=choices,
                    source=dict(request_id=i,file_id=record['file_id'],source_entry=entry,source_token_row=0,
                                source_layer_id=source_layer,sha256=record['file']['sha256']))
                tokens[record['rank_id'],record['slot']]=token
                for expert in choices:
                    dst=group[expert//(256//len(group))];matrix[record['rank_id'],dst]+=1;received[dst]+=1
                source_rows+=1
            actual_tokens={(row['rank'],row['slot']):row for row in wave['routes'][local_layer]['tokens']}
            require(actual_tokens==tokens and len(actual_tokens)==len(wave['routes'][local_layer]['tokens']),
                    'wave choices or source indices differ from raw file')
            require(wave['routes'][local_layer]['contributions']==[dict(src=a,dst=b,count=count) for (a,b),count in sorted(matrix.items())],
                    'wave contribution matrix differs from original choices')
            for (src,dst),count in matrix.items():
                if src!=dst:
                    size=count*7168*m['activation_bytes'];expected[local_layer,'dispatch',src,dst]=size
                    expected[local_layer,'combine',dst,src]=size
            expert_work[local_layer]=received
        actual={(v['layer'],v['phase'],v['src'],v['dst']):v['bytes'] for v in wave['messages']}
        require(actual==expected and len(actual)==len(wave['messages']),'source-to-network byte conservation failed')
        for message in wave['messages']:
            identity=message['id'];require(identity not in descriptors,'message identity reused')
            descriptors[identity]=message;flow=flows[identity];cb=callbacks[identity]
            require(all(flow[k]==message[k] for k in ('src','dst','bytes','plane')) and flow['ack_callbacks']==1,
                    'native message does not match graph')
            require(flow['receiver_contiguous_bytes']==flow['source_unique_data_payload_bytes']==message['bytes'] and
                    flow['source_data_payload_bytes']>=message['bytes'] and flow['sender_qp_present'] is False,
                    'native source/accepted bytes or release differ')
            require(history['start_ns']<=flow['ready_ns']<=flow['first_tx_ns']<=flow['receive_ready_ns']<=flow['ack_ns']<=history['finished_ns'],
                    'native message time escaped graph')
            require(cb['send_post_ns']==flow['ready_ns'] and cb['send_callback_ns']==flow['sender_resource_released_ns'] and
                    cb['recv_callback_ns']==max(cb['recv_post_ns'],flow['receive_ready_ns']),'native completion callbacks differ')
            current=message['src']
            for hop in flow['first_packet_path']:
                require(hop['node']==current,'packet path is not contiguous')
                if topology_nodes[current]['kind']!='gpu':require(topology_nodes[current]['plane']==message['plane'],'packet crossed planes')
                current=ports[current,hop['port']]['peer']
            require(current==message['dst'],'first packet path did not reach destination')
            total_bytes+=message['bytes']
        for rank,tasks in wave['rank_nodes'].items():
            rank=int(rank);by_id={v['id']:v for v in tasks};require(len(by_id)==len(tasks),'duplicate ASTRA node')
            expected_comp={}
            for layer in range(m['layers']):
                for stage,key in (('attention','attention_ns_per_token'),('pack','pack_ns_per_token'),('merge','merge_ns_per_token')):
                    expected_comp[f'L{layer}.{stage}']=source_tokens[rank]*cost[key]
                expected_comp[f'L{layer}.experts']=expert_work[layer][rank]*cost['expert_ns_per_contribution']
            require({task['name']:task['duration_ns'] for task in tasks if task['kind']=='COMP'}==expected_comp,
                    'idle source costs or received-expert compute differ')
            for task in tasks:
                start=event_time(events,graph,rank,task['id'],'issue');end=event_time(events,graph,rank,task['id'],'callback')
                require(history['start_ns']<=start<=end<=history['finished_ns'],'node escaped graph')
                require(all(dep in by_id and start>=event_time(events,graph,rank,dep,'callback') for dep in task['deps']),
                        'ASTRA predecessor did not complete')
                if task['kind']=='COMP':require(end-start==task['duration_ns'],'declared computation did not execute')
                else:
                    cb=callbacks[task['message']['id']];direction='send' if task['kind']=='SEND' else 'recv'
                    require(start==cb[direction+'_post_ns'] and end==cb[direction+'_callback_ns'],'ASTRA/native callback differs')
                nodes_checked+=1
    require(set(descriptors)==set(flows),'extra native work outside submitted SimAI graphs')
    for index in range(len(groups)):
        rows=sorted([row for row in f['history'] if row['group_index']==index],key=lambda row:row['step'])
        require([row['step'] for row in rows]==list(range(w['decode_steps'])) and rows[0]['start_ns']==0 and
                all(b['start_ns']==a['finished_ns'] for a,b in zip(rows,rows[1:])),
                'next SimAI wave did not follow actual preceding completion')
    from .scaleout_recovery import audit
    recovery=audit(r)
    from .scaleout_data_loss import audit as audit_loss
    loss=audit_loss(r)
    from .service_terminal import service_terminal
    terminal=service_terminal(r,f,require_complete=True)
    return dict(passed=True,frontend='actual_SimAI_Vidur',simai_revision=receipt['revision'],gpu_count=n,planes=planes,
        source_requests=len(replay.records),active_source_ranks=len(expected_sources),source_token_layer_rows=source_rows,
        waves=len(f['history']),messages=len(flows),remote_payload_bytes=total_bytes,verified_nodes=nodes_checked,
        finish_ns=r['finish_ns'],recovery=recovery,data_loss=loss,hardware_calibrated=False,
        route_source=c['routes']['source']['kind'],
        scope='finite_ready_KV_MoE_segment; source-checked choices; single native ns3 fabric',
        active_data_ranks=len({rank for v in r['messages'] for rank in (v['src'],v['dst'])}),**terminal)
