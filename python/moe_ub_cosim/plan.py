"""The single-layer direct_rank_dedup_v1 reference model; no execution scheduler."""
from collections import Counter, defaultdict
from copy import deepcopy
from .locality import LOCAL_PROFILE, FABRIC_PROFILE, validate_deployment, locality, contributions


MAX_TASK_BYTES = 2**32 - 1
MAX_TIME_NS = 2**63 - 1
MAX_RANK_ID = 2**31 - 2  # ASTRA uses signed int ranks and allocates id + 1 entries.
MAX_TRANSFERS = 4096  # Explicit bound for this small functional graph driver.
MAX_BATCH_TOKENS = 4096  # Host-side functional graph budget, not an EP restriction.


def batch_limit(config):
    limits = config['batch_limits']
    require(isinstance(limits, dict) and set(limits) == {'max_num_seqs', 'max_num_batched_tokens'},
            'Invalid batch limits')
    for name, value in limits.items():
        integer(value, name, 1, MAX_BATCH_TOKENS)
    return min(limits.values())


def compute_inputs(config):
    """Actual shapes passed to the selected reference or operator profile."""
    experts = Counter(e for token in config['tokens'] for e in token['expert_ids'])
    profiled = 'operator_profile' in config
    return dict(basis='exact_operator_profile_with_reference_residual' if profiled else 'linear_reference_per_token_and_expert_input',
        context_sensitive=profiled, expert_distribution_sensitive=profiled,
        hidden_dim=config['hidden_dim'], dtypes=deepcopy(config['compute_dtypes']),
        ranks=[dict(rank_id=r,
            requests=[dict(request_id=t['request_id'], token_index=t['token_index'],
                           context_tokens=t['token_index']+1, query_tokens=1)
                      for t in config['tokens'] if t['src_rank'] == r],
            batch_size=sum(t['src_rank'] == r for t in config['tokens']),
            expert_inputs=[dict(expert_id=e, input_tokens=experts[e])
                           for e, owner in enumerate(config['expert_placement']) if owner == r])
            for r in config['rank_ids']])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, field, minimum=0, maximum=MAX_TIME_NS):
    require(type(value) is int and minimum <= value <= maximum, f'Invalid {field}: {value!r}')
    return value


def validate_config(c):
    cann = c.get('template') == 'cann_a3_v850_fullmesh_v1'
    expected = {'schema_version', 'graph_id', 'wave_id', 'service_id', 'role', 'group_id', 'ready_ns',
                'layer_id', 'rank_ids', 'rank_to_ub_node', 'hidden_dim', 'top_k',
                'dispatch_element_bytes', 'combine_element_bytes', 'application_metadata_bytes',
                'chunk_bytes', 'send_slots_per_rank', 'template', 'expert_placement', 'tokens',
                'compute_profile_ns'}
    batched = c.get('schema_version') == 'moe-ub-layer-fixture/3'
    physical = c.get('schema_version') == 'moe-ub-layer-fixture/2' or (batched and 'deployment' in c)
    require(set(c) == expected | ({'deployment'} if physical else set()) |
            ({'batch_limits', 'compute_dtypes'} if batched else set()) |
            ({'operator_profile'} if batched and 'operator_profile' in c else set()) |
            ({'cann_profile'} if cann and batched else set()) and
            c['schema_version'] in ('moe-ub-layer-fixture/1', 'moe-ub-layer-fixture/2', 'moe-ub-layer-fixture/3'),
            'Unsupported fixture schema/fields')
    if batched:
        limit = batch_limit(c)
        require(c['role'] == 'D', 'Batched layer requires ready-KV decode')
        dtypes = c['compute_dtypes']
        require(isinstance(dtypes, dict) and set(dtypes) == {'compute', 'kv', 'dispatch', 'combine'}, 'Invalid compute dtypes')
        sizes = dict(float16=2, bfloat16=2, float32=4, **({'int8':1} if cann else {}))
        require(all(isinstance(v, str) for v in dtypes.values()) and
                all(dtypes[k] in sizes for k in ('compute','dispatch','combine')) and
                dtypes['kv'] in dict(sizes, fp8=1), 'Invalid compute dtype')
        require(all(sizes[dtypes[k]] == c[k+'_element_bytes'] for k in ('dispatch','combine')), 'Compute dtype/vector mismatch')
    for key in ('graph_id', 'wave_id', 'service_id', 'group_id'):
        require(isinstance(c[key], str) and bool(c[key]), f'Empty {key}')
    require(c['role'] in ('P', 'D'), 'Require role P/D')
    require(isinstance(c['rank_ids'], list) and bool(c['rank_ids']), 'Require nonempty group members')
    require(isinstance(c['rank_to_ub_node'], list) and
            len(c['rank_to_ub_node']) == len(c['rank_ids']), 'Incomplete UB mapping')
    for rank in c['rank_ids']:
        integer(rank, 'rank_id', 0, MAX_RANK_ID)
    for node in c['rank_to_ub_node']:
        integer(node, 'UB node', 0, 2**32 - 1)
    require(len(set(c['rank_to_ub_node'])) == len(c['rank_ids']), 'Duplicate UB endpoint')
    require(c['rank_ids'] == sorted(set(c['rank_ids'])), 'Group members must be distinct and sorted')
    if physical:
        validate_deployment(c['deployment'], c['rank_ids'])
    require(cann or c['template'] == 'direct_rank_dedup_v1', 'Unsupported communication template')
    integer(c['send_slots_per_rank'], 'send_slots_per_rank', 1, 2)
    integer(c['application_metadata_bytes'], 'application_metadata_bytes', 0, 0)
    integer(c['ready_ns'], 'ready_ns', 0, MAX_TIME_NS - 1000000)
    integer(c['layer_id'], 'layer_id', 0, 2**31 - 1)
    integer(c['hidden_dim'], 'hidden_dim', 1)
    for field in ('dispatch_element_bytes', 'combine_element_bytes'):
        integer(c[field], field, 1, 16)
        integer(c['hidden_dim'] * c[field], field + ' vector bytes', 1, MAX_TIME_NS)
    integer(c['chunk_bytes'], 'chunk_bytes', 1, MAX_TASK_BYTES)
    placement = c['expert_placement']
    require(isinstance(placement, list) and bool(placement), 'Require nonempty expert placement')
    integer(c['top_k'], 'top_k', 1, len(placement))
    for rank in placement:
        integer(rank, 'expert placement rank', 0, MAX_RANK_ID)
        require(rank in c['rank_ids'], 'Expert is outside its EP group')
    profile = c['compute_profile_ns']
    require(set(profile) == {'attention_route_pack', 'expert_per_input', 'expert_local_merge',
                             'combine_unpack_merge', 'next_layer'}, 'Unsupported compute profile fields')
    for key, duration in profile.items():
        integer(duration, key, 0 if key == 'next_layer' or (cann and key == 'expert_local_merge') else 1,
                MAX_TIME_NS - c['ready_ns'])
    tokens = c['tokens']
    require(isinstance(tokens, list) and 1 <= len(tokens) <= (MAX_BATCH_TOKENS if batched else 16 if c['role'] == 'P' else len(c['rank_ids'])),
            'Token count outside bounded P/D graph')
    ranks, identities = [], set()
    for token in tokens:
        require(set(token) == {'request_id', 'src_rank', 'phase', 'token_index', 'layer_id', 'expert_ids'},
                'Unsupported Token fields')
        require(isinstance(token['request_id'], str) and bool(token['request_id']), 'Invalid request_id')
        integer(token['src_rank'], 'Token src_rank', 0, MAX_RANK_ID)
        integer(token['token_index'], 'Token token_index', 0, MAX_TIME_NS-1 if batched else MAX_TIME_NS)
        integer(token['layer_id'], 'Token layer_id', 0, 2**31 - 1)
        require(token['phase'] == ('prefill' if c['role'] == 'P' else 'decode') and
                token['layer_id'] == c['layer_id'], 'Token phase/layer mismatch')
        experts = token['expert_ids']
        require(isinstance(experts, list) and len(experts) == c['top_k'], 'Missing or wrong Top-K route')
        for expert in experts:
            integer(expert, 'expert_id', 0, len(placement) - 1)
        require(len(set(experts)) == len(experts), 'Duplicate expert choice')
        identity = (token['request_id'], token['phase'], token['token_index'], token['layer_id'])
        require(identity not in identities, 'Duplicate Token identity')
        identities.add(identity)
        ranks.append(token['src_rank'])
    require((batched or c['role'] == 'P' or len(set(ranks)) == len(ranks)) and set(ranks) <= set(c['rank_ids']),
            'Require at most one local Token per group rank')
    local_counts = Counter(ranks)
    if batched:
        require(all(n <= limit for n in local_counts.values()), 'Batch exceeds rank sequence/token budget')
        require(len({t['request_id'] for t in tokens}) == len(tokens), 'Decode request appears more than once in this layer iteration')
    if cann:
        from .cann import validate_config as validate_cann
        validate_cann(c)
        return
    expert_counts = Counter(placement[e] for token in tokens for e in token['expert_ids'])
    from .operator_profile import durations
    measured = durations(c)
    for rank in c['rank_ids']:
        duration = (local_counts[rank] * sum(profile[k] for k in
                    ('attention_route_pack', 'combine_unpack_merge', 'next_layer')) +
                    expert_counts[rank] * profile['expert_per_input'] +
                    bool(expert_counts[rank]) * profile['expert_local_merge'])
        if measured:
            duration += measured[rank]['attention_decode']['duration_ns'] + measured[rank]['expert_ffn']['duration_ns'] - expert_counts[rank]*profile['expert_per_input']
        if physical:
            for kind in ('dispatch', 'combine'):
                count = sum(e['locality'] == 'same_chip' and e['src_rank' if kind == 'dispatch' else 'dst_rank'] == rank
                            for e in contributions(c))
                duration += count * c['deployment']['local_prepare_ns_per_vector'][kind]
        integer(duration, 'rank computation duration', 0, MAX_TIME_NS - c['ready_ns'])


def build_plan(config, identities=None, *, _validate=True):
    validate_config(config)
    if config['template'] == 'cann_a3_v850_fullmesh_v1':
        from .cann import build_plan as build_cann
        return build_cann(config, identities, _validate=_validate)
    c = deepcopy(config)
    from .operator_profile import durations
    measured = durations(c)
    operations = [dict(operation_id=f"{c['wave_id']}.L{c['layer_id']}.{kind}", kind=kind,
                       service_id=c['service_id'], role=c['role'], wave_id=c['wave_id'],
                       layer_id=c['layer_id'], group_id=c['group_id'], participants=c['rank_ids'])
                  for kind in ('dispatch', 'combine')]
    expert_inputs = {rank: [] for rank in c['rank_ids']}
    remote = defaultdict(list)
    for source_index, token in enumerate(c['tokens']):
        by_rank = defaultdict(list)
        for expert in token['expert_ids']:
            dst = c['expert_placement'][expert]
            item = dict(request_id=token['request_id'], phase=token['phase'], token_index=token['token_index'],
                        source_record_index=source_index, layer_id=token['layer_id'],
                        src_rank=token['src_rank'], expert_id=expert, local=dst == token['src_rank'])
            expert_inputs[dst].append(item)
            by_rank[dst].append(expert)
        for dst, experts in sorted(by_rank.items()):
            if dst != token['src_rank']:
                remote[token['src_rank'], dst].append(dict(request_id=token['request_id'],
                    phase=token['phase'], token_index=token['token_index'], source_record_index=source_index,
                    layer_id=token['layer_id'], expert_ids=sorted(experts)))
    transfers = []
    for op in operations:
        matrix = remote if op['kind'] == 'dispatch' else {(dst, src): v for (src, dst), v in remote.items()}
        for (src, dst), sources in sorted(matrix.items()):
            body = len(sources) * c['hidden_dim'] * c[op['kind'] + '_element_bytes']
            integer(body, 'directed vector bytes', 1, MAX_TIME_NS)
            local = locality(c, src, dst) == 'same_chip'
            limit = body if local else c['chunk_bytes']
            count = (body + limit - 1) // limit
            require(len(transfers) + count <= MAX_TRANSFERS, 'Functional plan exceeds 4096 transfers')
            for index, offset in enumerate(range(0, body, limit)):
                size = min(limit, body - offset)
                ordinal = len(transfers)
                transfers.append(dict(transfer_id=2**32 + 100 + ordinal, ub_task_id=100 + ordinal,
                    comm_tag=100 + ordinal, operation_id=op['operation_id'], kind=op['kind'],
                    src_rank=src, dst_rank=dst, payload_bytes=size, business_payload_bytes=size,
                    application_metadata_bytes=0, chunk_index=index, task_byte_range=[0, size],
                    pair_byte_range=[offset, offset + size], pair_payload_bytes=body,
                    transport_profile_id=LOCAL_PROFILE if local else FABRIC_PROFILE, priority=None if local else 7, sources=sources))
                if local:
                    del transfers[-1]['ub_task_id']
                    transfers[-1]['task_byte_range'] = None
    rank_graphs = []
    for rank in c['rank_ids']:
        nodes = []
        def comp(name, duration, deps, inputs=None):
            if duration == 0:
                return None  # No dummy work or zero-duration compute event.
            node = dict(node_id=len(nodes) + 1, rank_id=rank, kind='COMP', name=name,
                        duration_ns=duration, resource_id='gpu_compute', deps=list(deps))
            if inputs is not None:
                node['expert_inputs'] = inputs
            nodes.append(node)
            return node['node_id']
        local_prepares = {}
        def communication(kind, predecessors):
            local_ready = predecessors
            if 'deployment' in c:
                count = sum(len(t['sources']) for t in transfers if t['kind'] == kind and
                            t['src_rank'] == rank and t['transport_profile_id'] == LOCAL_PROFILE)
                prepare_id = comp(kind + '-local-prepare',
                    count*c['deployment']['local_prepare_ns_per_vector'][kind], predecessors)
                local_prepares[kind] = prepare_id
                if prepare_id is not None:
                    local_ready = [prepare_id]
            ids = []
            for direction in ('SEND', 'RECV'):
                mine = sorted((t for t in transfers if t['kind'] == kind and
                               t['src_rank' if direction == 'SEND' else 'dst_rank'] == rank),
                              key=lambda t: (t['dst_rank' if direction == 'SEND' else 'src_rank'], t['chunk_index']))
                for t in mine:
                    node_id = len(nodes) + 1
                    nodes.append(dict(node_id=node_id, rank_id=rank, kind=direction,
                        name=f"{kind}-{direction.lower()}-{t['src_rank']}-{t['dst_rank']}",
                        transfer_id=t['transfer_id'], deps=list(local_ready if direction == 'SEND' and
                        t['transport_profile_id'] == LOCAL_PROFILE else predecessors)))
                    t['send_node_id' if direction == 'SEND' else 'recv_node_id'] = node_id
                    ids.append(node_id)
            return ids
        profile = c['compute_profile_ns']
        local_tokens = sum(t['src_rank'] == rank for t in c['tokens'])
        prepare_ns = local_tokens * profile['attention_route_pack']
        expert_ns = len(expert_inputs[rank]) * profile['expert_per_input']
        if measured:
            prepare_ns += measured[rank]['attention_decode']['duration_ns']
            expert_ns = measured[rank]['expert_ffn']['duration_ns']
        prepare = comp('attention-route-pack', prepare_ns, [])
        prepared = [prepare] if prepare is not None else []
        dispatch = communication('dispatch', prepared)
        expert = comp('expert-compute', expert_ns,
                      prepared + dispatch, expert_inputs[rank])
        computed = [expert] if expert is not None else prepared + dispatch
        local_merge = comp('expert-local-merge', bool(expert_inputs[rank]) * profile['expert_local_merge'], computed)
        merged = [local_merge] if local_merge is not None else computed
        combine = communication('combine', merged)
        finish = comp('combine-unpack-merge', local_tokens * profile['combine_unpack_merge'], merged + combine)
        finished = [finish] if finish is not None else merged + combine
        next_layer = comp('next-layer', local_tokens * profile['next_layer'], finished)
        rank_graphs.append(dict(rank_id=rank, nodes=nodes, phase_nodes=dict(prepare=prepare,
            expert=expert, local_merge=local_merge, finish=finish, next_layer=next_layer)))
        if 'deployment' in c:
            rank_graphs[-1]['local_prepare_nodes'] = local_prepares
    plan = dict(schema_version='moe-ub-execution-plan/3' if c['schema_version']=='moe-ub-layer-fixture/3' else
                'moe-ub-execution-plan/2' if 'deployment' in c else 'moe-ub-execution-plan/1', graph_id=c['graph_id'], wave_id=c['wave_id'],
        rank_ids=c['rank_ids'], rank_to_ub_node=c['rank_to_ub_node'], ready_ns=c['ready_ns'],
        instance_batches=[dict(rank_id=r, request_ids=list(dict.fromkeys(t['request_id'] for t in c['tokens'] if t['src_rank'] == r)))
                          for r in c['rank_ids']],
        communication_template=c['template'], communication_profile_basis='reference_model',
        compute_source='synthetic_fixed_duration', route_source='explicit_synthetic_fixture',
        calibration_status='unvalidated_reference', sender_completion='transaction_done',
        send_slots_per_rank=c['send_slots_per_rank'], operations=operations, transfers=transfers, rank_graphs=rank_graphs,
        expert_input_counts=[len(v) for v in expert_inputs.values()], input=c)
    if 'deployment' in c:
        plan['deployment'] = deepcopy(c['deployment'])
        plan['contributions'] = contributions(c)
    if c['schema_version'] == 'moe-ub-layer-fixture/3':
        plan['batch_limits'] = deepcopy(c['batch_limits'])
        plan['compute_inputs'] = compute_inputs(c)
    if measured:
        from .operator_profile import evaluate
        plan['compute_evaluation'] = evaluate(c)
        plan['compute_source'] = c['operator_profile']['basis']+'_with_reference_residual'
    if identities is not None:
        identities.assign(plan)
    if _validate:
        validate_plan(plan)
    return plan


def validate_plan(plan):
    if plan.get('schema_version') == 'moe-ub-full-model-plan/1':
        from .full_model import validate_full_model
        return validate_full_model(plan)
    if plan.get('schema_version') in ('moe-ub-multilayer-plan/1','moe-ub-multilayer-plan/2'):
        from .multilayer import validate_multilayer
        return validate_multilayer(plan)
    if plan.get('communication_template') == 'kv_direct_v1':
        from .kv_plan import validate_kv_plan
        return validate_kv_plan(plan)
    if plan.get('communication_template') == 'cann_a3_v850_fullmesh_v1':
        from .cann import validate_plan as validate_cann
        return validate_cann(plan)
    batched = plan.get('schema_version') == 'moe-ub-execution-plan/3'
    physical = plan.get('schema_version') == 'moe-ub-execution-plan/2' or (batched and 'deployment' in plan)
    require(plan['schema_version'] in ('moe-ub-execution-plan/1', 'moe-ub-execution-plan/2', 'moe-ub-execution-plan/3'), 'Unsupported plan schema')
    require(plan['input']['schema_version'] == ('moe-ub-layer-fixture/3' if batched else
            'moe-ub-layer-fixture/2' if physical else 'moe-ub-layer-fixture/1') and
            physical == ('deployment' in plan['input']), 'Plan/input schema mismatch')
    ranks = plan['rank_ids']
    require(ranks == plan['input']['rank_ids'] and
            plan['rank_to_ub_node'] == plan['input']['rank_to_ub_node'], 'Invalid rank mapping')
    integer(plan['send_slots_per_rank'], 'send_slots_per_rank', 1, 2)
    validate_config(plan['input'])
    if batched:
        require(plan['batch_limits'] == plan['input']['batch_limits'] and
                plan['compute_inputs'] == compute_inputs(plan['input']), 'Batch limits or compute inputs mismatch')
    require(('compute_evaluation' in plan) == ('operator_profile' in plan['input']), 'Profile evaluation missing or unexpected')
    if 'operator_profile' in plan['input']:
        from .operator_profile import evaluate
        require(plan['compute_evaluation'] == evaluate(plan['input']), 'Operator profile evaluation mismatch')
    require(plan['send_slots_per_rank'] == plan['input']['send_slots_per_rank'], 'Send slot configuration mismatch')
    operations = {op['operation_id']: op for op in plan['operations']}
    require(len(operations) == len(plan['operations']) == 2, 'Duplicate or missing operation')
    for op in operations.values():
        require(op['participants'] == ranks, 'Operation participants mismatch')
    transfers = {t['transfer_id']: t for t in plan['transfers']}
    require(len(transfers) == len(plan['transfers']) <= MAX_TRANSFERS, 'Duplicate/too many transfers')
    for field in ('comm_tag', 'ub_task_id'):
        values = [t[field] for t in transfers.values() if field in t]
        require(len(set(values)) == len(values), f'Duplicate {field}')
    for t in transfers.values():
        integer(t['transfer_id'], 'transfer_id', 1, 2**64 - 1)
        local = locality(plan['input'], t['src_rank'], t['dst_rank']) == 'same_chip'
        if local:
            require('ub_task_id' not in t, 'Local delivery has a UB task')
        else:
            integer(t['ub_task_id'], 'ub_task_id', 0, 2**32 - 1)
        # The locked ASTRA NATIVE path remaps tags >= 500000000; do not truncate.
        integer(t['comm_tag'], 'comm_tag', 1, 499999999)
        integer(t['payload_bytes'], 'payload_bytes', 1, MAX_TIME_NS if local else MAX_TASK_BYTES)
        require(t['src_rank'] in ranks and t['dst_rank'] in ranks and t['src_rank'] != t['dst_rank'], 'Invalid transfer endpoints')
        require(t['operation_id'] in operations and t['kind'] == operations[t['operation_id']]['kind'], 'Unknown operation')
        require(t['business_payload_bytes'] == t['payload_bytes'] and t['application_metadata_bytes'] == 0 and
                t['task_byte_range'] == (None if local else [0, t['payload_bytes']]) and
                t['priority'] == (None if local else 7) and
                t['transport_profile_id'] == (LOCAL_PROFILE if local else FABRIC_PROFILE), 'Invalid payload/profile')
    grouped = defaultdict(list)
    for t in transfers.values():
        integer(t['chunk_index'], 'chunk_index', 0, MAX_TRANSFERS - 1)
        grouped[t['operation_id'], t['src_rank'], t['dst_rank']].append(t)
    expected_pairs = Counter()
    expected_sources = defaultdict(list)
    for record, token in enumerate(plan['input']['tokens']):
        src = token['src_rank']
        for dst in {plan['input']['expert_placement'][e] for e in token['expert_ids']} - {src}:
            source = {key: token[key] for key in ('request_id', 'phase', 'token_index', 'layer_id')}
            source.update(source_record_index=record, expert_ids=sorted(
                e for e in token['expert_ids'] if plan['input']['expert_placement'][e] == dst))
            for key in [('dispatch', src, dst), ('combine', dst, src)]:
                expected_pairs[key] += 1
                expected_sources[key].append(source)
    actual_pairs = {(parts[0]['kind'], src, dst): parts for (_, src, dst), parts in grouped.items()}
    require(set(actual_pairs) == set(expected_pairs), 'Transfer set differs from Token routes')
    for key, parts in actual_pairs.items():
        require(parts[0]['sources'] == expected_sources[key], 'Transfer sources differ from Token routes')
        require(sum(t['payload_bytes'] for t in parts) == expected_pairs[key] * plan['input']['hidden_dim'] *
                plan['input'][key[0] + '_element_bytes'], 'Transfer bytes differ from Token routes')
    for parts in grouped.values():
        first = parts[0]
        expected = len(first['sources']) * plan['input']['hidden_dim'] * plan['input'][first['kind'] + '_element_bytes']
        offset = 0
        for index, t in enumerate(parts):
            limit = expected if first['transport_profile_id'] == LOCAL_PROFILE else plan['input']['chunk_bytes']
            size = min(limit, expected - offset)
            require(size > 0 and t['chunk_index'] == index and t['payload_bytes'] == size and
                    t['pair_payload_bytes'] == expected and t['pair_byte_range'] == [offset, offset + size] and
                    t['sources'] == first['sources'], 'Invalid chunk sequence, range, tail or source')
            offset += size
        require(offset == expected, 'Incomplete directed vector')
    require([g['rank_id'] for g in plan['rank_graphs']] == ranks, 'Missing rank graph')
    pairs = Counter()
    for graph in plan['rank_graphs']:
        rank, seen = graph['rank_id'], set()
        for node in graph['nodes']:
            integer(node['node_id'], 'node_id', 1)
            require(node['node_id'] not in seen and node['rank_id'] == rank, 'Duplicate node or wrong rank')
            require(len(set(node['deps'])) == len(node['deps']) and set(node['deps']) <= seen,
                    'Dependencies must be acyclic, local and topologically ordered')
            seen.add(node['node_id'])
            if node['kind'] == 'COMP':
                integer(node['duration_ns'], 'duration_ns', 1)
                require(node['resource_id'] == 'gpu_compute', 'Unknown compute resource')
            else:
                require(node['kind'] in ('SEND', 'RECV') and node['transfer_id'] in transfers, 'Unknown communication node')
                t = transfers[node['transfer_id']]
                side = 'send' if node['kind'] == 'SEND' else 'recv'
                require(rank == t['src_rank' if side == 'send' else 'dst_rank'] and
                        node['node_id'] == t[side + '_node_id'], 'Communication identity mismatch')
                pairs[node['transfer_id'], side] += 1
    require(all(pairs[t, side] == 1 for t in transfers for side in ('send', 'recv')), 'Missing or duplicate SEND/RECV')

    if physical or batched:
        expected = build_plan(plan['input'], _validate=False)
        mapping = {}
        for actual, canonical in zip(plan['transfers'], expected['transfers']):
            mapping[canonical['transfer_id']] = actual['transfer_id']
            for key in ('transfer_id', 'comm_tag', 'ub_task_id'):
                if key in canonical:
                    canonical[key] = actual[key]
        for graph in expected['rank_graphs']:
            for node in graph['nodes']:
                if 'transfer_id' in node:
                    node['transfer_id'] = mapping[node['transfer_id']]
        for key in ('transfers', 'rank_graphs', 'expert_input_counts') + (('deployment', 'contributions') if physical else ()):
            require(plan[key] == expected[key], 'Canonical plan mismatch: ' + key)


class RunIdentities:
    """Allocate bounded run identities atomically; local graph node ids stay local."""
    def __init__(self, transfer=2**32 + 100, task=100, tag=100):
        self.transfer, self.task, self.tag = transfer, task, tag
        self.graphs, self.operations = set(), set()

    def assign(self, plan):
        count = len(plan['transfers'])
        task_count = sum('ub_task_id' in t for t in plan['transfers'])
        operations = {op['operation_id'] for op in plan['operations']}
        require(plan['graph_id'] not in self.graphs, 'Reused graph_id')
        require(not self.operations & operations, 'Reused operation_id')
        for start, maximum, name in ((self.transfer, 2**64 - 1, 'transfer_id'),
                                      (self.task, 2**32 - 1, 'ub_task_id'),
                                      (self.tag, 499999999, 'comm_tag')):
            integer(start, name, 1, maximum)
            require((task_count if name == 'ub_task_id' else count) <= maximum - start + 1, name + ' allocator exhausted')
        mapping = {}
        task_index = 0
        for index, transfer in enumerate(plan['transfers']):
            mapping[transfer['transfer_id']] = self.transfer + index
            transfer.update(transfer_id=self.transfer + index, comm_tag=self.tag + index)
            if 'ub_task_id' in transfer:
                transfer['ub_task_id'] = self.task + task_index
                task_index += 1
        for graph in plan['rank_graphs']:
            for node in graph['nodes']:
                if 'transfer_id' in node:
                    node['transfer_id'] = mapping[node['transfer_id']]
        # Layer components retain local node ids, but use the same run-wide
        # transfer/task/tag identities as the composed executable graph.
        if 'layers' in plan:
            assigned = {t['transfer_id']: t for t in plan['transfers']}
            for layer in plan['layers']:
                for transfer in layer['transfers']:
                    current = assigned[mapping[transfer['transfer_id']]]
                    for key in ('transfer_id', 'ub_task_id', 'comm_tag'):
                        if key in current:
                            transfer[key] = current[key]
                for graph in layer['rank_graphs']:
                    for node in graph['nodes']:
                        if 'transfer_id' in node:
                            node['transfer_id'] = mapping[node['transfer_id']]
        self.transfer += count; self.task += task_count; self.tag += count
        self.graphs.add(plan['graph_id']); self.operations.update(operations)
