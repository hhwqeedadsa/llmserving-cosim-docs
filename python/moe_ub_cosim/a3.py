"""A3 STORE reference with source-derived cores and explicit completion events.

Local read completion and remote copy fences are declared mappings onto actual
native execution events. They are not calibrated MTE or memory service times.
"""
from collections import Counter, defaultdict
from copy import deepcopy

from . import cann
from .plan import integer, require, batch_limit, MAX_TIME_NS

TEMPLATE = 'cann_a3_v850_store_resources_v1'
ALGORITHM = 'a3_fullmesh_v1_store_resources_tp1'


def layout_config(config):
    c = deepcopy(config)
    p = c['cann_profile']
    c['template'] = cann.TEMPLATE
    c['send_slots_per_rank'] = 1  # Private layout construction, never executed.
    p['aiv_lanes'] = p.pop('platform_aiv_cores')
    p.pop('core_count_basis')
    p.pop('copy_fence')
    p['algorithm'] = 'a3_fullmesh_v1_dynamic_int8_tp1'
    return c


def validate_config(c):
    p = c['cann_profile']
    require(isinstance(p, dict) and set(p) == {'source_revision', 'algorithm',
        'platform_aiv_cores', 'core_count_basis', 'copy_fence', 'window_bytes', 'local_cost_ns'},
        'Invalid A3 resource profile')
    require(p['algorithm'] == ALGORITHM and p['core_count_basis'] == 'declared_platform_reference',
        'A3 requires explicit platform core-count basis')
    integer(p['platform_aiv_cores'], 'platform AIV cores', 1, 48)
    require(p['copy_fence'] in ('data_ready', 'local_read_complete'), 'Unknown A3 copy fence')
    integer(c['send_slots_per_rank'], 'A3 protocol-owned send resource marker', 0, 0)
    from .plan import validate_config as validate_layer
    validate_layer(layout_config(c))
    resource_description(c)


def resource_description(c):
    """D.Init/QuantInit for dynamic INT8, prefix token mask, no expert mask."""
    p = c['cann_profile']; cores = p['platform_aiv_cores']
    h, b, k, e = c['hidden_dim'], batch_limit(c), c['top_k'], len(c['expert_placement'])
    a = cann.align
    state = a((e+cores-1)//cores, 8)*32
    indices = a(b*k*4, 32)
    temporary = max(h*4, indices, b*a(k, 32)*2, a(b*k*2, 256)//2)
    used = state + indices + 3*temporary
    candidate = used + 2*a(h*2, 32) + 2*a(a(h, 32)+44, 32)
    buffers = 1 if candidate > 170*1024 else 2
    return dict(platform_aiv_cores=cores, core_count_basis=p['core_count_basis'],
        dispatch_buffers_per_core=buffers, combine_buffers_per_core=2,
        dispatch_buffer_selection_bytes=candidate, dispatch_selection_limit_bytes=170*1024,
        local_read_completion='native_requests_created_all_useful_bytes', copy_fence=p['copy_fence'],
        send_capacity='native_worker_outstanding_and_node_request_id_pool',
        compute_resources='one_per_aiv_and_one_gpu_compute', hardware_calibrated=False)


def apply_schedule(p, config):
    """Reuse the byte layout, replacing every execution dependency explicitly."""
    c = deepcopy(config); cp = c['cann_profile']; ranks = c['rank_ids']
    cores = cp['platform_aiv_cores']; info = resource_description(c)
    cost = cp['local_cost_ns']; profile = c['compute_profile_ns']; k = c['top_k']
    position = {r:i for i,r in enumerate(ranks)}
    own = defaultdict(list); remote = defaultdict(list); tokens = defaultdict(list)
    for i,t in enumerate(c['tokens']): tokens[t['src_rank']].append(i)
    for x in p['contributions']:
        own[x['src_rank']].append(x); remote[x['dst_rank']].append(x)
    vector = defaultdict(list); states = {}; incoming = defaultdict(list)
    for t in p['transfers']:
        if 'ub_task_id' in t:
            t.update(operation='STORE', initiator_rank=t['src_rank'], data_owner_rank=t['src_rank'],
                consumer_rank=t['dst_rank'], transport_profile_id='native-store-default')
        if t['sources']:
            s = t['sources'][0]
            vector[t['kind'],t['message_role'],t['src_rank'],s['source_record_index'],s['topk_index']].append(t)
        else: states[t['src_rank'],t['expert_id']] = t
        incoming[t['kind'],t['dst_rank']].append(t)
    graphs = []; cost_config = layout_config(c)
    for rank in ranks:
        nodes = []; members = defaultdict(list); receives = {}
        extra = next((x for x in c.get('endpoint_prepare', {}).get('ranks', []) if x['rank_id']==rank), {})
        local_cost = c.get('deployment', {}).get('local_prepare_ns_per_vector', {})
        def add(kind, name, deps, **fields):
            n = dict(node_id=len(nodes)+1, rank_id=rank, kind=kind, name=name,
                deps=sorted(set(deps)), **fields)
            nodes.append(n); return n['node_id']
        def event(name, deps, transfer=None, observed='barrier'):
            fields = dict(event=observed, resource_id='')
            if transfer is not None: fields['transfer_id'] = transfer['transfer_id']
            return add('EVENT', name, deps, **fields)
        def work(name, duration, deps, core=None, member=None):
            integer(duration, 'A3 local work', 0, MAX_TIME_NS-c['ready_ns'])
            result = (add('COMP', name, deps, duration_ns=duration,
                resource_id='gpu_compute' if core is None else f'aiv:{core}')
                if duration else event(name, deps))
            if member: members[member].append(result)
            return result
        def send(t, deps):
            node = add('SEND', t['kind']+'-'+t['message_role']+'-send', deps, transfer_id=t['transfer_id'])
            t['send_node_id'] = node
            read = event('local-read-complete', deps, t, 'local_read_complete')
            fence = read if cp['copy_fence']=='local_read_complete' else event('copy-fence', deps, t, 'data_ready')
            return read, fence
        def receive(kind, entry):
            for t in incoming[kind,rank]:
                n = add('RECV', kind+'-'+t['message_role']+'-recv', [entry], transfer_id=t['transfer_id'])
                t['recv_node_id'] = n; receives[t['transfer_id']] = n
        def copies(kind, x, prepared):
            out = vector[kind,'data',rank,x['source_record_index'],x['topk_index']]
            reads, fences = [], []
            for t in out:
                read, fence = send(t, [prepared]); reads.append(read); fences.append(fence)
            return event(kind+'-buffer-reusable', reads or [prepared]), event(kind+'-data-fence', fences or [prepared])
        entry = event('dispatch-entry', [])
        receive('dispatch', entry)
        previous = {}; buffers = {}; data_done = []
        ordinal = Counter()
        unit = profile['attention_route_pack']+extra.get('dispatch_ns_per_local_token',0)
        for core,x in cann.split(own[rank],cores):
            slot = ordinal[core] % info['dispatch_buffers_per_core']; ordinal[core] += 1
            deps = [entry]+([previous[core]] if core in previous else [])
            if (core,slot) in buffers: deps.append(buffers[core,slot])
            duration = unit//k+int(x['topk_index']<unit%k)
            if x['locality']=='same_chip': duration += local_cost.get('dispatch',0)
            prep = work('dispatch-quant-pack', duration, deps, core, 'prepare'); previous[core] = prep
            read, fence = copies('dispatch',x,prep); buffers[core,slot] = read; data_done.append(fence)
        data_barrier = event('dispatch-cross-core-data-barrier', [entry]+data_done+list(buffers.values()))
        status_done = []; previous = {}
        for core,e in cann.split(list(range(len(c['expert_placement']))),cores):
            deps = [data_barrier]+([previous[core]] if core in previous else [])
            t = states.get((rank,e))
            if t is not None:
                read,fence = send(t,deps); previous[core] = read; status_done.append(fence)
        arrived = event('dispatch-counts-and-data-ready', [data_barrier]+status_done+
            [receives[t['transfer_id']] for t in incoming['dispatch',rank]])
        owned_experts = len(c['expert_placement'])//len(ranks)
        block_counts = Counter((x['expert_id']%owned_experts)*len(ranks)+position[x['src_rank']] for x in remote[rank])
        per_core = Counter()
        for core,block in cann.split(list(range(len(c['expert_placement']))),cores):
            per_core[core] += cost['status_check']+block_counts[block]*cost['dispatch_reorder_per_input']
        reorders = [work('dispatch-reorder',v,[arrived],core,'reorder') for core,v in sorted(per_core.items())]
        dispatched = event('dispatch-operator-complete',reorders or [arrived])
        expert = work('expert-compute',len(remote[rank])*profile['expert_per_input'],[dispatched])
        combined_entry = event('combine-entry',[expert]); receive('combine',combined_entry)
        previous = {}; end_by_core = defaultdict(list); local_results = {}; buffers = {}; ordinal = Counter()
        items = sorted(remote[rank],key=lambda x:(x['expert_id'],position[x['src_rank']],x['source_record_index']))
        for core,x in cann.split(items,cores,position[rank]):
            slot = ordinal[core] % info['combine_buffers_per_core']; ordinal[core] += 1
            deps = [combined_entry]+([previous[core]] if core in previous else [])
            if (core,slot) in buffers: deps.append(buffers[core,slot])
            duration = cost['result_pack_per_input']+extra.get('combine_ns_per_expert_input',0)
            if x['locality']=='same_chip': duration += local_cost.get('combine',0)
            prep = work('combine-result-pack',duration,deps,core,'pack')
            read,fence = copies('combine',x,prep); buffers[core,slot] = read
            states_for_x = vector['combine','status',rank,x['source_record_index'],x['topk_index']]
            if states_for_x:
                status_read,status_fence = send(states_for_x[0],[fence]); previous[core] = status_read
                end_by_core[core].append(status_fence)
            else:
                previous[core] = fence; end_by_core[core].append(fence)
                local_results[x['source_record_index'],x['topk_index']] = fence
        core_end = {core:event('combine-core-sends-complete',[combined_entry]+end_by_core[core]) for core in range(cores)}
        previous = {}; reductions = []
        for core,i in cann.split(tokens[rank],cores):
            deps = [core_end[core]]+([previous[core]] if core in previous else [])
            token = c['tokens'][i]
            for j,e in enumerate(token['expert_ids']):
                owner = c['expert_placement'][e]
                if owner==rank: deps.append(local_results[i,j])
                else:
                    for role in ('data','status'):
                        deps += [receives[t['transfer_id']] for t in vector['combine',role,owner,i,j]]
            duration = profile['combine_unpack_merge']+k*(cost['status_check']+cost['reduction_per_input'])
            n = work('combine-token-reduction',duration,deps,core,'reduce'); previous[core] = n; reductions.append(n)
        finish = event('combine-operator-complete',reductions+list(core_end.values()))
        next_layer = work('next-layer',len(tokens[rank])*profile['next_layer'],[finish]) if profile['next_layer'] else None
        graph = dict(rank_id=rank,nodes=nodes,phase_nodes=dict(prepare=entry,dispatch_reorder=dispatched,
            expert=expert,local_merge=combined_entry,finish=finish,next_layer=next_layer),
            phase_members=dict(members),terminal_nodes=[next_layer or finish])
        expected = cann.stage_durations(cost_config,len(tokens[rank]),len(remote[rank]),
            sum(x['locality']=='same_chip' for x in own[rank]),sum(x['locality']=='same_chip' for x in remote[rank]),rank)
        total = sum(n.get('duration_ns',0) for n in nodes)
        require(total==sum(expected.values()), 'A3 split work is not conserved')
        graph['compute_work_ns'] = total; graphs.append(graph)
    p.update(input=c,communication_template=TEMPLATE,send_slots_per_rank=0,rank_graphs=graphs,a3_resources=info)
    p['communication_evidence'].update(transaction_mapping='platform_native_STORE_not_native_MTE',
        scheduling='source_cores_local_buffers_and_explicit_events_reference')
    p['communication_buffers']['staging_bytes'] = cores*(info['dispatch_buffers_per_core']*cann.align(cann.align(c['hidden_dim'],32)+44,32)+2*cann.align(c['hidden_dim']*2,32))
    p['communication_buffers']['total_bytes'] = p['communication_buffers']['data_window_bytes']+1048576+p['communication_buffers']['staging_bytes']
    from .resources import actual_usage, check_graph
    usage = check_graph(actual_usage(p),c.get('resource_budget'))
    if 'resource_budget' in c: p['resource_usage'] = usage
    return p


def build_plan(config, identities=None, *, _validate=True):
    validate_config(config)
    from .resources import check_graph, actual_usage
    expected_usage = check_graph(usage(config),config.get('resource_budget'))
    p = apply_schedule(cann.build_plan(layout_config(config),_validate=False),config)
    require(actual_usage(p)==expected_usage,'A3 graph preflight mismatch')
    if identities is not None: identities.assign(p)
    if _validate: validate_plan(p)
    return p


def usage(c):
    from .resources import cann_usage
    result = cann_usage(layout_config(c))
    cores = c['cann_profile']['platform_aiv_cores']
    fence = int(c['cann_profile']['copy_fence']=='data_ready')
    result['nodes'] = (len(c['rank_ids'])*(7+cores+min(cores,len(c['expert_placement'])))+
        len(c['tokens'])*(6*c['top_k']+1)+(3+fence)*result['transfers']+
        (len(c['rank_ids']) if c['compute_profile_ns']['next_layer'] else 0))
    return result


def validate_plan(plan):
    expected = build_plan(plan['input'],_validate=False)
    require(len(plan['transfers'])==len(expected['transfers']),'A3 transfer count mismatch')
    mapping = {}; seen = defaultdict(set)
    for actual,canonical in zip(plan['transfers'],expected['transfers']):
        mapping[canonical['transfer_id']] = actual['transfer_id']
        for key,lo,hi in [('transfer_id',1,2**64-1),('comm_tag',1,499999999),('ub_task_id',0,2**32-1)]:
            if key in canonical:
                integer(actual[key],key,lo,hi)
                require(actual[key] not in seen[key],'Repeated A3 identity');seen[key].add(actual[key]);canonical[key]=actual[key]
    for g in expected['rank_graphs']:
        for n in g['nodes']:
            if 'transfer_id' in n:n['transfer_id']=mapping[n['transfer_id']]
    for key,value in expected.items():
        if key not in ('route_source','compute_source'):require(plan.get(key)==value,'A3 canonical mismatch: '+key)
    require(set(plan)-{'input_provenance'}==set(expected),'Unknown A3 plan fields')
