"""Source-derived A3 fullmesh_v1 contributions; explicit reference scheduling.

CANN v8.5.0, TP1, dynamic int8 dispatch, unquantized BF16 combine. We model
remote stores using the selected WRITE/RTP completion contract, not MTE timing.
No native CANN code is executed. Source evidence and approximations are in docs.
"""
from collections import Counter
from copy import deepcopy

from .locality import locality, LOCAL_PROFILE, FABRIC_PROFILE
from .plan import integer, require, batch_limit, compute_inputs, MAX_TRANSFERS, MAX_TIME_NS

TEMPLATE = 'cann_a3_v850_fullmesh_v1'
REVISION = '6ead121aded45355043b502756b6592fd7c30b14'
COSTS = ('dispatch_reorder_per_input', 'status_check', 'result_pack_per_input', 'reduction_per_input')


def align(n, size):
    return (n+size-1)//size*size


def buffers(c):
    h, k, world, experts = c['hidden_dim'], c['top_k'], len(c['rank_ids']), len(c['expert_placement'])
    bs = batch_limit(c)
    # Host tiling reserves using the maximum two-byte output, even for int8.
    minimum = 2*bs*(experts*align(align(h*2,32)+44,512)+k*align(h*2,512))
    staging = c['cann_profile']['aiv_lanes']*2*align(align(h,32)+44,32)
    return dict(minimum_data_window_bytes=minimum, data_window_bytes=c['cann_profile']['window_bytes'],
        state_window_bytes=1048576, staging_bytes=staging,
        total_bytes=c['cann_profile']['window_bytes']+1048576+staging,
        reuse_rule='no_reuse_before_local_graph_completion', native_double_window_overlap_modeled=False,
        logical_input_slots_per_rank=bs, global_bs=bs*world,
        inactive_slots='masked_no_vector_traffic', dispatch_window_stride=align(align(h,32)+44,512),
        combine_window_stride=align(h*2,512))


def validate_config(c):
    require(c['schema_version']=='moe-ub-layer-fixture/3' and c['role']=='D', 'CANN requires batched decode layer/3')
    p = c['cann_profile']
    require(isinstance(p,dict) and set(p)=={'source_revision','algorithm','aiv_lanes','window_bytes','local_cost_ns'},
            'Invalid CANN profile fields')
    require(p['source_revision']==REVISION and p['algorithm']=='a3_fullmesh_v1_dynamic_int8_tp1',
            'Unsupported CANN revision/algorithm/TP/quantization')
    require('operator_profile' not in c, 'CANN operator profiles need matching stage coverage; not implemented')
    require(c['compute_dtypes']==dict(compute='bfloat16',kv='bfloat16',dispatch='int8',combine='bfloat16') and
            c['dispatch_element_bytes']==1 and c['combine_element_bytes']==2, 'CANN dtype mismatch')
    require(c['compute_profile_ns']['expert_local_merge']==0, 'CANN returns separate expert results; no remote merge')
    integer(c['hidden_dim'],'CANN H',1024,8192)
    integer(len(c['rank_ids']),'CANN EP',2,768)
    integer(c['top_k'],'CANN Top-K',1,16)
    integer(batch_limit(c),'CANN BS',1,512)
    e, world = len(c['expert_placement']), len(c['rank_ids'])
    require(0<e<=1024 and e%world==0 and
            c['expert_placement']==[r for r in c['rank_ids'] for _ in range(e//world)],
            'CANN expertShardType=0 requires contiguous equal expert ownership in EP rank order')
    integer(p['aiv_lanes'],'reference AIV lanes',1,48)
    integer(p['window_bytes'],'CANN window_bytes',1,MAX_TIME_NS)
    require(isinstance(p['local_cost_ns'],dict) and set(p['local_cost_ns'])==set(COSTS), 'Invalid CANN local costs')
    for k,v in p['local_cost_ns'].items(): integer(v,k,1,MAX_TIME_NS)
    b=buffers(c)
    require(b['data_window_bytes']>=b['minimum_data_window_bytes'], 'CANN data window below tiling bound')
    integer(b['total_bytes'],'CANN buffers',1,MAX_TIME_NS)


def split(items, lanes, rotate=0):
    """Balanced contiguous core partition; combine rotates within each core."""
    offset=0
    for lane in range(lanes):
        count=len(items)//lanes+int(lane<len(items)%lanes)
        part=items[offset:offset+count];offset+=count
        if part:
            shift=rotate%len(part)
            part=part[shift:]+part[:shift]
        for item in part: yield lane,item


def build_plan(config, identities=None, *, _validate=True):
    c=deepcopy(config);ranks=c['rank_ids'];h=c['hidden_dim'];cp=c['cann_profile'];lanes=cp['aiv_lanes']
    ops=[dict(operation_id=f"{c['wave_id']}.L{c['layer_id']}.{kind}",kind=kind,
        service_id=c['service_id'],role=c['role'],wave_id=c['wave_id'],layer_id=c['layer_id'],
        group_id=c['group_id'],participants=ranks) for kind in ('dispatch','combine')]
    opids={o['kind']:o['operation_id'] for o in ops}
    contrib=[]
    for i,t in enumerate(c['tokens']):
        for k,e in enumerate(t['expert_ids']):
            dst=c['expert_placement'][e]
            contrib.append(dict(source_record_index=i,topk_index=k,expert_id=e,
                src_rank=t['src_rank'],dst_rank=dst,locality=locality(c,t['src_rank'],dst)))
    counts=Counter(x['dst_rank'] for x in contrib);local=Counter(t['src_rank'] for t in c['tokens'])
    messages=[]
    def add(kind,role,src,dst,lane,source=None,expert=None,count=None):
        if src==dst:return
        index=len(messages)
        vector = role=='data'
        useful=h*c[kind+'_element_bytes'] if vector else 0
        if vector and kind=='dispatch':
            # h_align32 + reserved scale block32 + rank/token/topk tuple12.
            regions=[dict(begin=0,end=h,kind='business')]
            if align(h,32)>h:regions.append(dict(begin=h,end=align(h,32),kind='padding'))
            regions.append(dict(begin=align(h,32),end=align(h,32)+4,kind='metadata'))
            regions.append(dict(begin=align(h,32)+4,end=align(h,32)+32,kind='padding'))
            regions.append(dict(begin=align(h,32)+32,end=align(h,32)+44,kind='metadata'))
        else:regions=[dict(begin=0,end=useful if vector else 32,kind='business' if vector else 'metadata')]
        message=dict(message_id=index,kind=kind,message_role=role,src_rank=src,dst_rank=dst,lane=lane,
                     regions=regions,payload_bytes=regions[-1]['end'],sources=[])
        if source is not None:
            t=c['tokens'][source['source_record_index']]
            message['sources']=[dict(**{k:t[k] for k in ('request_id','phase','token_index','layer_id')},
                source_record_index=source['source_record_index'],expert_ids=[source['expert_id']],topk_index=source['topk_index'])]
        if expert is not None:message.update(expert_id=expert,token_count=count)
        messages.append(message)
    for rank in ranks:
        for lane,x in split([x for x in contrib if x['src_rank']==rank],lanes):
            add('dispatch','data',rank,x['dst_rank'],lane,x)
    for rank in ranks:
        for lane,e in split(list(range(len(c['expert_placement']))),lanes):
            add('dispatch','status',rank,c['expert_placement'][e],lane,expert=e,
                count=sum(x['src_rank']==rank and x['expert_id']==e for x in contrib))
    for rank in ranks:
        # LocalWindowCopy orders local expert, source EP rank, then source Token.
        items=sorted((x for x in contrib if x['dst_rank']==rank),
                     key=lambda x:(x['expert_id'],ranks.index(x['src_rank']),x['source_record_index']))
        for lane,x in split(items,lanes,ranks.index(rank)):
            add('combine','data',rank,x['src_rank'],lane,x)
            add('combine','status',rank,x['src_rank'],lane,x)
    transfers=[]
    for m in messages:
        same_chip=locality(c,m['src_rank'],m['dst_rank'])=='same_chip'
        # Status writes remain one 32-byte store, never split by vector chunks.
        limit=m['payload_bytes'] if same_chip or m['message_role']=='status' else c['chunk_bytes']
        for chunk,begin in enumerate(range(0,m['payload_bytes'],limit)):
            end=min(begin+limit,m['payload_bytes']);size=end-begin;regions=[]
            for region in m['regions']:
                a,b=max(begin,region['begin']),min(end,region['end'])
                if a<b:regions.append(dict(begin=a-begin,end=b-begin,kind=region['kind']))
            totals=Counter()
            for x in regions:totals[x['kind']]+=x['end']-x['begin']
            ordinal=len(transfers)
            t=dict(transfer_id=2**32+100+ordinal,comm_tag=100+ordinal,operation_id=opids[m['kind']],
                **{k:deepcopy(m[k]) for k in ('kind','message_role','message_id','lane','src_rank','dst_rank','sources')},
                payload_bytes=size,business_payload_bytes=totals['business'],application_metadata_bytes=totals['metadata'],
                padding_bytes=totals['padding'],payload_regions=regions,chunk_index=chunk,
                task_byte_range=None if same_chip else [0,size],pair_byte_range=[begin,end],
                pair_payload_bytes=m['payload_bytes'],transport_profile_id=LOCAL_PROFILE if same_chip else FABRIC_PROFILE,
                priority=None if same_chip else 7)
            if 'expert_id' in m:t.update(expert_id=m['expert_id'],token_count=m['token_count'])
            if not same_chip:t['ub_task_id']=100+ordinal
            transfers.append(t)
    require(len(transfers)<=MAX_TRANSFERS,'CANN plan exceeds 4096 transfer budget')
    graphs=[]
    for rank in ranks:
        nodes=[];cost=cp['local_cost_ns'];profile=c['compute_profile_ns']
        def comp(name,work,deps):
            if not work:return None
            integer(work,'CANN stage duration',1,MAX_TIME_NS-c['ready_ns'])
            nodes.append(dict(node_id=len(nodes)+1,rank_id=rank,kind='COMP',name=name,duration_ns=work,
                              resource_id='gpu_compute',deps=sorted(set(deps))))
            return nodes[-1]['node_id']
        def comm(t,direction,deps):
            node=len(nodes)+1
            nodes.append(dict(node_id=node,rank_id=rank,kind=direction,transfer_id=t['transfer_id'],
                name=f"{t['kind']}-{t['message_role']}-{direction.lower()}",deps=sorted(set(deps))))
            t['send_node_id' if direction=='SEND' else 'recv_node_id']=node
            return node
        def sends(kind,deps,local_ready):
            done=[];lane_last={};data_done=[]
            for t in transfers:
                if t['kind']!=kind or t['src_rank']!=rank:continue
                before=list(deps)
                if t['transport_profile_id']==LOCAL_PROFILE:before+=local_ready
                if t['lane'] in lane_last:before.append(lane_last[t['lane']])
                if kind=='dispatch' and t['message_role']=='status':before+=data_done
                node=comm(t,'SEND',before);lane_last[t['lane']]=node;done.append(node)
                if t['message_role']=='data':data_done.append(node)
            return done
        def receives(kind,deps):
            return [comm(t,'RECV',deps) for t in transfers if t['kind']==kind and t['dst_rank']==rank]
        prepare=comp('attention-route-quant-pack',local[rank]*profile['attention_route_pack'],[])
        ready=[] if prepare is None else [prepare]
        local_prepares={}
        def local_prepare(kind,deps):
            n=sum(x['locality']=='same_chip' and x['src_rank' if kind=='dispatch' else 'dst_rank']==rank for x in contrib)
            work=n*c.get('deployment',{}).get('local_prepare_ns_per_vector',{}).get(kind,0)
            node=comp(kind+'-local-prepare',work,deps);local_prepares[kind]=node
            return [] if node is None else [node]
        prep_local=local_prepare('dispatch',ready)
        dispatch=ready+sends('dispatch',ready,prep_local)+receives('dispatch',ready)+prep_local
        reorder=comp('dispatch-status-check-reorder',counts[rank]*cost['dispatch_reorder_per_input']+
                     len(c['expert_placement'])*cost['status_check'],dispatch)
        expert=comp('expert-compute',counts[rank]*profile['expert_per_input'],[reorder])
        computed=[expert] if expert is not None else [reorder]
        pack=comp('expert-result-pack',counts[rank]*cost['result_pack_per_input'],computed)
        packed=[pack] if pack is not None else computed
        combine_local=local_prepare('combine',packed)
        combine=packed+sends('combine',packed,combine_local)+receives('combine',packed)+combine_local
        finish=comp('combine-token-weighted-reduction',local[rank]*(profile['combine_unpack_merge']+
                    c['top_k']*(cost['status_check']+cost['reduction_per_input'])),combine)
        final=[finish] if finish is not None else combine
        next_layer=comp('next-layer',local[rank]*profile['next_layer'],final)
        integer(c['ready_ns']+sum(n.get('duration_ns',0) for n in nodes),'CANN rank time bound')
        graphs.append(dict(rank_id=rank,nodes=nodes,phase_nodes=dict(prepare=prepare,dispatch_reorder=reorder,
            expert=expert,local_merge=pack,finish=finish,next_layer=next_layer)))
        if 'deployment' in c:graphs[-1]['local_prepare_nodes']=local_prepares
    plan=dict(schema_version='moe-ub-execution-plan/3',graph_id=c['graph_id'],wave_id=c['wave_id'],
        rank_ids=ranks,rank_to_ub_node=c['rank_to_ub_node'],ready_ns=c['ready_ns'],
        instance_batches=[dict(rank_id=r,request_ids=[t['request_id'] for t in c['tokens'] if t['src_rank']==r]) for r in ranks],
        communication_template=TEMPLATE,communication_profile_basis='runtime_adapted',
        communication_evidence=dict(source_revision=REVISION,source_path='mc2/moe_distribute_dispatch_v2/op_kernel/moe_distribute_dispatch_v2.h',
            combine_source_path='mc2/moe_distribute_combine_v2/op_kernel/moe_distribute_combine_v2.h',
            transaction_mapping='URMA_WRITE/RTP_reference_not_native_MTE',
            scheduling='balanced_core_lanes_with_transaction_done_serialization_reference',
            target_runtime_executed=False,local_cost_basis='declared_reference'),
        compute_source='synthetic_fixed_duration',route_source='explicit_synthetic_fixture',calibration_status='unvalidated_reference',
        sender_completion='transaction_done',send_slots_per_rank=c['send_slots_per_rank'],operations=ops,
        transfers=transfers,rank_graphs=graphs,expert_input_counts=[counts[r] for r in ranks],input=c,
        contributions=contrib,communication_buffers=buffers(c),batch_limits=c['batch_limits'],compute_inputs=compute_inputs(c))
    if 'deployment' in c:plan['deployment']=deepcopy(c['deployment'])
    if identities is not None:identities.assign(plan)
    if _validate:validate_plan(plan)
    return plan


def validate_plan(plan):
    from .plan import validate_config as validate_layer
    validate_layer(plan['input'])
    expected=build_plan(plan['input'],_validate=False)
    require(len(plan['transfers'])==len(expected['transfers']),'CANN transfer count mismatch')
    mapping={};seen={k:set() for k in ('transfer_id','comm_tag','ub_task_id')}
    for actual,canonical in zip(plan['transfers'],expected['transfers']):
        mapping[canonical['transfer_id']]=actual['transfer_id']
        for k,limit in (('transfer_id',2**64-1),('comm_tag',499999999),('ub_task_id',2**32-1)):
            if k in canonical:
                integer(actual[k],k,0 if k=='ub_task_id' else 1,limit)
                require(actual[k] not in seen[k],'CANN reused '+k);seen[k].add(actual[k]);canonical[k]=actual[k]
    for graph in expected['rank_graphs']:
        for n in graph['nodes']:
            if 'transfer_id' in n:n['transfer_id']=mapping[n['transfer_id']]
    for k,value in expected.items():
        if k not in ('route_source','compute_source'):
            require(plan.get(k)==value,'CANN canonical mismatch: '+k)
