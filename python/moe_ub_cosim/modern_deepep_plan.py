"""Untimed modern direct DeepEP operations with symbolic concurrent slots.

This compiler creates no WQEs, NICs, CUDA kernels or packet timing. Visibility
and completion events are obligations for a future provider, not observations.
"""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import shutil

from .deepep_plan import read, encoded, reachable, fields, integer, require

REVISION = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NCCL_REVISION = '12df1a11afad322be5a204a2db890161cbf8131d'
MAX_INPUT, MAX_OUTPUT = 2**20, 16*2**20


def align(n, multiple):
    return ((n+multiple-1)//multiple)*multiple


def token_bytes(hidden, topk, dispatch):
    metadata = 8*topk + (4+4*topk if dispatch else 0)
    return align(2*hidden+align(metadata, 32), 64)


def context_assignment(index, execution, rank):
    connections = execution['connections']
    devices = execution['local_devices'][rank]
    connection = index % connections
    return dict(context_index=index, connection=connection, local_context=index//connections,
                local_device=devices[connection % len(devices)])


def qp_mode(sm, channel, sms, contexts, channels_per_sm, notify):
    if contexts == 1:
        return 0, 'cta' if sms == 1 else 'gpu'
    start = int(notify)
    available = contexts-start
    if sms <= available:
        count = available//sms+int(sm < available%sms)
        return start+sm+(channel%count)*sms, 'cta'
    return start+(sm*channels_per_sm+channel)%available, 'cta' if sms == 1 else 'gpu'


def resolve_combine_channel(binding, slot, execution, rank):
    """Resolve ONLY after a declared/actual slot exists; never pick its order."""
    integer(slot, 0, binding['source_to_owner_count']-1, 'resolved Dispatch slot')
    index = binding['source_rank_prefix']+slot
    per_warp = (binding['owner_received_tokens']+execution['num_sms']*execution['combine_warps']-1)//(
        execution['num_sms']*execution['combine_warps'])
    global_channel = index//per_warp
    sm, warp = global_channel%execution['num_sms'], global_channel//execution['num_sms']
    context, sharing = qp_mode(sm, warp, execution['num_sms'], execution['call_contexts'],
                               execution['combine_warps'], False)
    return dict(sm=sm, rotated_warp=warp, physical_warp=(warp-rank)%execution['combine_warps'],
                sharing=sharing, **context_assignment(context, execution, rank))


def validate(config):
    fields(config, 'schema_version model execution calls limits', 'modern root')
    require(config['schema_version'] == 'moe-modern-deepep-input/1', 'unsupported modern schema')
    m, x, limits = (config[k] for k in ('model', 'execution', 'limits'))
    fields(m, 'experts top_k hidden_size precision', 'modern model')
    integer(m['experts'], 2, 2048, 'experts')
    integer(m['top_k'], 1, min(32, m['experts']), 'top_k')
    integer(m['hidden_size'], 256, 8192, 'hidden_size')
    require(m['hidden_size']%256 == 0 and m['precision'] == 'bf16', 'require BF16 hidden divisible by 256')
    fields(x, 'ranks max_tokens_per_rank mode stream expanded_layout allow_multiple_reduction '
           'expert_alignment num_sms dispatch_warps combine_warps call_contexts requested_contexts '
           'connections local_devices', 'modern execution')
    integer(x['ranks'], 2, 32, 'ranks')
    integer(x['max_tokens_per_rank'], 1, 16, 'max_tokens_per_rank')
    require(m['experts']%x['ranks'] == 0 and m['experts']//x['ranks'] <= 512, 'invalid experts/rank')
    require(x['mode'] == 'direct_rdma_only_fresh' and x['stream'] == 'compute_wait_no_deferred_epilogue',
            'only fresh direct RDMA-only, compute-wait and nondeferred epilogue are supported')
    require(x['expanded_layout'] is True and x['allow_multiple_reduction'] is True,
            'require expanded layout with local reduction')
    require(type(x['expert_alignment']) is int and x['expert_alignment'] == 1, 'expert padding unsupported')
    for key, maximum in [('num_sms', 8), ('dispatch_warps', 8), ('combine_warps', 8),
                         ('call_contexts', 64), ('requested_contexts', 64), ('connections', 4)]:
        integer(x[key], 1, maximum, key)
    require(x['call_contexts'] <= x['requested_contexts'], 'call contexts exceed requested allocation')
    require(isinstance(x['local_devices'], list) and len(x['local_devices']) == x['ranks'],
            'require each rank local device table')
    for devices in x['local_devices']:
        require(isinstance(devices, list) and 1 <= len(devices) <= 4, 'invalid local devices')
        for device in devices:
            integer(device, 0, 65535, 'declared local device id')
    fields(limits, 'max_events max_operations max_input_bytes max_output_bytes', 'modern limits')
    for key, maximum in [('max_events', 65536), ('max_operations', 16384),
                         ('max_input_bytes', MAX_INPUT), ('max_output_bytes', MAX_OUTPUT)]:
        integer(limits[key], 1, maximum, key)
    require(isinstance(config['calls'], list) and 1 <= len(config['calls']) <= 4, 'require 1..4 calls')
    names = set()
    for call in config['calls']:
        fields(call, 'name tokens', 'modern call')
        require(isinstance(call['name'], str) and 0 < len(call['name']) <= 64 and call['name'] not in names,
                'invalid/repeated call name')
        names.add(call['name'])
        require(isinstance(call['tokens'], list) and len(call['tokens']) == x['ranks'], 'incomplete rank rows')
        for tokens in call['tokens']:
            require(isinstance(tokens, list) and len(tokens) <= x['max_tokens_per_rank'], 'too many tokens')
            for choices in tokens:
                require(isinstance(choices, list) and len(choices) == m['top_k'] and
                        all(type(e) is int and 0 <= e < m['experts'] for e in choices) and
                        len(set(choices)) == len(choices), 'invalid/masked/repeated expert choices')


def compile_plan(config):
    validate(config)
    m, x, limits = (config[k] for k in ('model', 'execution', 'limits'))
    ranks, cap = x['ranks'], x['max_tokens_per_rank']
    local_experts = m['experts']//ranks
    dbytes, cbytes = (token_bytes(m['hidden_size'], m['top_k'], d) for d in (True, False))
    planes = min(ranks, m['top_k'])
    events, operations, slots = [], [], []
    seen, previous = set(), {}
    history = defaultdict(list)

    def event(name, kind, rank, deps=(), **attrs):
        require(name not in seen and len(events) < limits['max_events'], 'event identity/budget exceeded')
        deps = sorted(set(deps))
        require(all(d in seen for d in deps), 'missing/forward modern dependency')
        seen.add(name);events.append(dict(id=name, kind=kind, rank=rank, deps=deps, **attrs))
        return name

    def operation(name, ci, phase, kind, src, dst, nbytes, deps, binding, target,
                  visible_deps=(), **attrs):
        require(len(operations) < limits['max_operations'], 'modern operation budget exceeded')
        issued = event(name+'.issued', 'issued', src, deps, operation=name)
        accepted = event(name+'.accepted', 'accepted', dst, [issued], operation=name)
        visible = event(name+'.visible', 'visible', dst, [accepted, *visible_deps], operation=name)
        complete = event(name+'.local_complete', 'local_complete', src, [accepted], operation=name)
        # Peer equality is not an API bypass: NCCLGin.signal and bulk put still
        # enter GDAKI on a self peer. Only explicit symmetric-pointer branches
        # avoid provider work. A self-peer QP is NOT a switch/fabric route.
        path = ('rdma' if src != dst else
                'gin_self' if attrs['verb'].startswith('gin_') else 'local')
        row = dict(id=name, call=ci, phase=phase, kind=kind, src=src, dst=dst, operand_bytes=nbytes,
                   path=path, binding=binding, target=target,
                   events=dict(issued=issued, accepted=accepted, visible=visible, local_complete=complete), **attrs)
        operations.append(row);history[src].append(row)
        return row

    def barrier(ci, phase, which, begins, producers=None):
        gates = dict(begins)
        # Include entry/previous-epoch signals: their local CQ completion can
        # still be pending after their target-visible signal has been waited.
        covered = {r:list(history[r]) for r in range(ranks)}
        if producers is not None:
            gates = {r:event(f'm{ci}.{phase}.{which}.r{r}.flush', 'flush_all_contexts', r,
                [producers[r], *[op['events']['local_complete'] for op in covered[r]]],
                contexts=list(range(x['call_contexts'])),
                covers_operations=[op['id'] for op in covered[r]]) for r in range(ranks)}
        signals = {}
        number = 4*ci + (0 if phase == 'dispatch' else 2) + (1 if which == 'entry' else 2)
        for src in range(ranks):
            for dst in range(ranks):
                visible = [op['events']['visible'] for op in covered[src]
                           if op['dst'] == dst] if producers is not None else []
                signals[src, dst] = operation(f'm{ci}.{phase}.{which}.s{src}.d{dst}', ci, phase,
                    'barrier_signal', src, dst, 8, [gates[src]], context_assignment(0,x,src),
                    dict(region='gin_signal_slots', slot=src, rolling_target=number),
                    visible_deps=visible, verb='gin_signal_inc64',
                    value=1, atomic_wire_bytes_unknown=True)
        return {r:event(f'm{ci}.{phase}.{which}.r{r}.ready', 'barrier_ready', r,
                [gates[r], *[signals[s,r]['events']['visible'] for s in range(ranks)]],
                rolling_target=number, visibility_contract='required provider obligation; cross-NIC uncalibrated')
                for r in range(ranks)}

    for ci, call in enumerate(config['calls']):
        groups, expert_counts = {}, Counter()
        pair_count = Counter()
        for src, tokens in enumerate(call['tokens']):
            for token, choices in enumerate(tokens):
                for expert in choices:
                    expert_counts[src,expert] += 1
                destinations = sorted({e//local_experts for e in choices})
                for dst in destinations:
                    groups[src,token,dst] = [i for i,e in enumerate(choices) if e//local_experts == dst]
                    pair_count[src,dst] += 1
        begin = {r:event(f'm{ci}.dispatch.r{r}.begin', 'kernel_begin', r,
                         [previous[r]] if ci else []) for r in range(ranks)}
        start = barrier(ci, 'dispatch', 'entry', begin)
        data, counts, role_last = {}, {}, {}
        for src, tokens in enumerate(call['tokens']):
            for token, choices in enumerate(tokens):
                channel = token%(x['num_sms']*x['dispatch_warps'])
                sm, warp = channel%x['num_sms'], channel//x['num_sms']
                context, sharing = qp_mode(sm,warp,x['num_sms'],x['call_contexts'],x['dispatch_warps'],True)
                ready = event(f'm{ci}.dispatch.s{src}.t{token}.prepared', 'token_prepared', src,
                    [start[src]]+([role_last[src,channel]] if (src,channel) in role_last else []))
                issued = []
                for dst in sorted({e//local_experts for e in choices}):
                    variable = f'm{ci}.slot.s{src}.d{dst}.t{token}'
                    allocated = event(variable, 'slot_allocate', src, [ready],
                                      allocation_group=[ci,src,dst], capacity=cap)
                    slots.append(dict(id=variable, call=ci, src=src, dst=dst, token=token,
                        group_size=pair_count[src,dst], constraints='bijection onto 0..group_size-1; runtime atomic order'))
                    op = operation(f'm{ci}.dispatch.data.s{src}.t{token}.d{dst}', ci, 'dispatch',
                        'token_data', src,dst,dbytes,[allocated],
                        dict(sm=sm,warp=warp,sharing=sharing,**context_assignment(context,x,src)),
                        dict(region='ep_buffer', epoch=2*ci, rank_segment=src,
                             byte_offset_base=src*cap*dbytes, byte_stride=dbytes, slot_variable=variable),
                        verb='gin_put' if src != dst else 'local_copy', token=token,
                        topk_positions=groups[src,token,dst], slot_variable=variable,
                        source=dict(region='ep_buffer',epoch=2*ci,byte_offset=(ranks*cap+token)*dbytes))
                    data[src,token,dst] = op;issued.append(op['events']['issued'])
                role_last[src,channel] = event(f'm{ci}.dispatch.s{src}.t{token}.issued',
                                                'channel_iteration_issued',src,issued)
        for src in range(ranks):
            values = []
            for dst in range(ranks):
                op = operation(f'm{ci}.dispatch.rank_count.s{src}.d{dst}', ci,'dispatch','rank_count',
                    src,dst,8,[start[src]],context_assignment(0,x,src),
                    dict(region='workspace_recv_rank_counts',element_offset=src,element_bytes=8,epoch=2*ci),
                    verb='gin_put_value' if src != dst else 'local_store', value=-pair_count[src,dst]-1,
                    aggregate_requests=True)
                counts[src,dst,'rank'] = op;values.append(op['events']['issued'])
            for dst in range(ranks):
                block = [-expert_counts[src,e]-1 for e in range(dst*local_experts,(dst+1)*local_experts)]
                counts[src,dst,'expert'] = operation(f'm{ci}.dispatch.expert_count.s{src}.d{dst}',
                    ci,'dispatch','expert_count_block',src,dst,8*local_experts,[start[src],*values],
                    context_assignment(0,x,src),
                    dict(region='workspace_recv_expert_counts',element_offset=src*local_experts,
                         element_bytes=8,epoch=2*ci),
                    verb='gin_put',values=block,aggregate_requests=False,
                    source=dict(region='workspace_send_expert_counts',element_offset=dst*local_experts,
                                element_bytes=8,epoch=2*ci))
        counted = {r:event(f'm{ci}.dispatch.r{r}.counts_ready','counts_ready',r,
            [counts[s,r,k]['events']['visible'] for s in range(ranks) for k in ('rank','expert')]) for r in range(ranks)}
        producers = {r:event(f'm{ci}.dispatch.r{r}.producer_join','producer_join',r,
            [counted[r],*[op['events']['issued'] for op in data.values() if op['src']==r]]) for r in range(ranks)}
        finished = barrier(ci,'dispatch','exit',start,producers)
        dispatch_done = {}
        for r in range(ranks):
            epilogue = event(f'm{ci}.dispatch.r{r}.epilogue','dispatch_expansion_done',r,[finished[r]])
            cleanup = event(f'm{ci}.dispatch.r{r}.cleanup','sender_counter_reset',r,[finished[r]])
            dispatch_done[r] = event(f'm{ci}.dispatch.r{r}.complete','application_stage_complete',r,[epilogue,cleanup])
        experts = {r:event(f'm{ci}.r{r}.experts_done','expert_stage_done',r,[dispatch_done[r]]) for r in range(ranks)}
        begin = {r:event(f'm{ci}.combine.r{r}.begin','kernel_begin',r,[experts[r]]) for r in range(ranks)}
        start = barrier(ci,'combine','entry',begin)
        returns = []
        for (original,token,owner), positions in sorted(groups.items()):
            slot = data[original,token,owner]['slot_variable']
            reduced = event(f'm{ci}.combine.s{owner}.d{original}.t{token}.reduced',
                            'local_expert_reduction_done',owner,[start[owner],experts[owner]],topk_positions=positions)
            binding = dict(kind='from_dispatch_slot',slot_variable=slot,
                           source_rank_prefix=sum(pair_count[s,owner] for s in range(original)),
                           source_to_owner_count=pair_count[original,owner],
                           owner_received_tokens=sum(pair_count[s,owner] for s in range(ranks)))
            plane = owner if ranks <= m['top_k'] else positions[0]
            returns.append(operation(f'm{ci}.combine.data.s{owner}.d{original}.t{token}',ci,'combine',
                'token_data',owner,original,cbytes,[reduced],binding,
                dict(region='ep_buffer',epoch=2*ci+1,byte_offset=(plane*cap+token)*cbytes),
                verb='gin_put' if owner != original else 'local_copy',token=token,topk_positions=positions,
                source=dict(region='local_reduction' if owner==original else 'ep_buffer',epoch=2*ci+1,
                            byte_offset=(planes*cap+original*cap+token)*cbytes)))
        producers = {r:event(f'm{ci}.combine.r{r}.producer_join','producer_join',r,
                    [start[r],*[op['events']['issued'] for op in returns if op['src']==r]]) for r in range(ranks)}
        finished = barrier(ci,'combine','exit',start,producers)
        previous = {r:event(f'm{ci}.combine.r{r}.complete','application_stage_complete',r,
                    [finished[r]],epilogue='local_final_reduction') for r in range(ranks)}
    summary = Counter()
    for op in operations:
        key = op['path']+'_'+op['phase']+'_'+op['kind']
        summary[key+'_operations'] += 1;summary[key+'_operand_bytes'] += op['operand_bytes']
    return dict(schema_version='moe-modern-deepep-partial-order/2',
        source=dict(deepep=REVISION,nccl=NCCL_REVISION), operations=operations,events=events,slots=slots,
        path_semantics=dict(local='explicit local pointer/store bypass; no GIN request',
            gin_self='GIN request to self-peer QP; provider resources required; physical loopback and cost unmodeled',
            rdma='GIN request to another rank; physical route and NIC reachability not validated'),
        layout=dict(dispatch_put_bytes=dbytes,combine_put_bytes=cbytes,
            dispatch_buffer_bytes=(ranks+1)*cap*dbytes,
            combine_buffer_bytes=(planes+ranks)*cap*cbytes,
            shared_ep_buffer_allocation_bytes=align(max((ranks+1)*cap*dbytes,(planes+ranks)*cap*cbytes),2*2**20),
            allocated_contexts=align(x['requested_contexts'],x['connections'])),
        deferred_constraints=[
            'Resolve each allocation_group as a slot bijection when its allocation events actually run',
            'Resolve Combine channels from source_rank_prefix plus the actual Dispatch slot',
            'For each Combine owner/SM/rotated-warp issue iterations in ascending resolved receive index',
            'The DAG alone does not execute these dynamic constraints or reserve WQEs'],
        summary=dict(summary), scope=dict(network_executed=False,cuda_executed=False,latency_predicted=False,
            hardware_calibrated=False,source_buffer_lifetime_validated=False,kernel_shape_support_validated=False,
            dynamic_slots_resolved=False,physical_qps_created=False,
            visibility='required provider obligations, not measured events; no cross-NIC calibration',
            concurrent_order='incomparable operations have no preselected WQE, slot or issue order',
            unknowns=['GPU launch/scheduling cost','provider WQE/atomic splitting and CQ modes',
                      'cross-NIC visibility and memory ordering','retry/buffer lifetime','topology reachability',
                      'self-peer GIN loopback service and physical path']))


def execute(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    raw, config = read(config_path, MAX_INPUT);validate(config)
    require(len(raw) <= config['limits']['max_input_bytes'], 'modern input budget exceeded')
    require(not output.exists() and output.parent.is_dir(), 'require new output directory with existing parent')
    require(shutil.disk_usage(output.parent).free >= 8*2**30+MAX_OUTPUT, 'reserve 8GiB')
    plan = compile_plan(config)
    require(len(reachable(plan)) == len(plan['events']), 'cyclic modern plan')
    plan_raw = encoded(plan)
    manifest = dict(schema_version='moe-modern-deepep-manifest/2',state='offline_symbolic_partial_order_only',
                    input_sha256=hashlib.sha256(raw).hexdigest(),plan_sha256=hashlib.sha256(plan_raw).hexdigest(),
                    event_count=len(plan['events']),operation_count=len(plan['operations']),slot_count=len(plan['slots']),
                    plan_bytes=len(plan_raw),summary=plan['summary'],simulated_packets=False)
    manifest_raw = encoded(manifest)
    require(len(raw)+len(plan_raw)+len(manifest_raw) <= config['limits']['max_output_bytes'], 'modern output budget exceeded')
    output.mkdir();(output/'input.json').write_bytes(raw)
    (output/'plan.json').write_bytes(plan_raw);(output/'manifest.json').write_bytes(manifest_raw)
    return dict(passed=True,output=str(output),**manifest)


def check(directory, output, withheld=()):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    require(not output.exists() and output.parent.is_dir(), 'require new check file with existing parent')
    raw, plan = read(directory/'plan.json',MAX_OUTPUT)
    original, config = read(directory/'input.json',MAX_INPUT)
    manifest_raw, manifest = read(directory/'manifest.json',MAX_INPUT)
    fields(manifest,'schema_version state input_sha256 plan_sha256 event_count operation_count slot_count '
           'plan_bytes summary simulated_packets','modern manifest')
    require(manifest['schema_version'] == 'moe-modern-deepep-manifest/2',
            'require modern manifest/2; recompile saved input to include self-peer GIN operations')
    require(manifest['state']=='offline_symbolic_partial_order_only' and manifest['simulated_packets'] is False,
            'modern manifest must declare offline, unsimulated scope')
    require(hashlib.sha256(raw).hexdigest()==manifest['plan_sha256'] and
            hashlib.sha256(original).hexdigest()==manifest['input_sha256'],'modern saved hash mismatch')
    require(encoded(compile_plan(config))==raw,'modern plan differs from compiler')
    require(len(raw)+len(original)+len(manifest_raw)<=config['limits']['max_output_bytes'] and
            len(original)<=config['limits']['max_input_bytes'],'modern saved budget exceeded')
    require(manifest['event_count']==len(plan['events']) and manifest['operation_count']==len(plan['operations'])
            and manifest['slot_count']==len(plan['slots']) and manifest['plan_bytes']==len(raw)
            and manifest['summary']==plan['summary'],'modern manifest count mismatch')
    require(len(reachable(plan))==len(plan['events']),'cyclic modern saved plan')
    done = reachable(plan,withheld)
    completions = [e['id'] for e in plan['events'] if e['kind']=='application_stage_complete']
    result = dict(passed=True,scope='symbolic representation integrity and untimed dependency closure',
        event_count=len(plan['events']),reachable_events=len(done),withheld=sorted(set(withheld)),
        blocked_completions=[e for e in completions if e not in done],
        reachable_completions=[e for e in completions if e in done],
        all_completions_reachable=all(e in done for e in completions),simulated_packets=False,
        slots_resolved=False,latency_predicted=False,hardware_calibrated=False,plan_sha256=manifest['plan_sha256'])
    output.write_bytes(encoded(result));return result
