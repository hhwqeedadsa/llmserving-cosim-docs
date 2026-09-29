"""The single-layer direct_rank_dedup_v1 reference model; no execution scheduler."""
from collections import Counter, defaultdict
from copy import deepcopy


MAX_TASK_BYTES = 2**32 - 1
MAX_TIME_NS = 2**63 - 1
MAX_TRANSFERS = 4096  # Explicit bound for this small functional graph driver.


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, field, minimum=0, maximum=MAX_TIME_NS):
    require(type(value) is int and minimum <= value <= maximum, f'Invalid {field}: {value!r}')
    return value


def validate_config(c):
    expected = {'schema_version', 'graph_id', 'wave_id', 'service_id', 'role', 'group_id', 'ready_ns',
                'layer_id', 'rank_ids', 'rank_to_ub_node', 'hidden_dim', 'top_k',
                'dispatch_element_bytes', 'combine_element_bytes', 'application_metadata_bytes',
                'chunk_bytes', 'send_slots_per_rank', 'template', 'expert_placement', 'tokens',
                'compute_profile_ns'}
    require(set(c) == expected and c['schema_version'] == 'moe-ub-layer-fixture/1', 'Unsupported fixture schema/fields')
    for key in ('graph_id', 'wave_id', 'service_id', 'group_id'):
        require(isinstance(c[key], str) and bool(c[key]), f'Empty {key}')
    require(c['role'] == 'D' and c['rank_ids'] == c['rank_to_ub_node'],
            'Require identity UB mapping and role D')
    require(isinstance(c['rank_ids'], list) and len(c['rank_ids']) == 4, 'Require four group members')
    for field in ('rank_ids', 'rank_to_ub_node'):
        require(all(type(rank) is int and 0 <= rank < 8 for rank in c[field]), f'Invalid integer {field}')
    require(c['rank_ids'] == sorted(set(c['rank_ids'])), 'Group members must be distinct and sorted')
    require(c['template'] == 'direct_rank_dedup_v1', 'Unsupported communication template')
    integer(c['send_slots_per_rank'], 'send_slots_per_rank', 1, 2)
    integer(c['application_metadata_bytes'], 'application_metadata_bytes', 0, 0)
    integer(c['top_k'], 'top_k', 2, 2)
    integer(c['ready_ns'], 'ready_ns', 0, MAX_TIME_NS - 1000000)
    integer(c['layer_id'], 'layer_id', 0, 2**31 - 1)
    integer(c['hidden_dim'], 'hidden_dim', 1)
    for field in ('dispatch_element_bytes', 'combine_element_bytes'):
        integer(c[field], field, 1, 16)
        integer(c['hidden_dim'] * c[field], field + ' vector bytes', 1, MAX_TIME_NS)
    integer(c['chunk_bytes'], 'chunk_bytes', 1, MAX_TASK_BYTES)
    placement = c['expert_placement']
    require(isinstance(placement, list) and len(placement) == 8, 'Require eight expert placements')
    for rank in placement:
        integer(rank, 'expert placement rank', 0, 7)
        require(rank in c['rank_ids'], 'Expert is outside its EP group')
    profile = c['compute_profile_ns']
    require(set(profile) == {'attention_route_pack', 'expert_per_input', 'expert_local_merge',
                             'combine_unpack_merge', 'next_layer'}, 'Unsupported compute profile fields')
    for key, duration in profile.items():
        integer(duration, key, 1, (MAX_TIME_NS - c['ready_ns']) // 16)
    tokens = c['tokens']
    require(isinstance(tokens, list) and len(tokens) == 4, 'M2.1 requires four explicit Token records')
    ranks, identities = [], set()
    for token in tokens:
        require(set(token) == {'request_id', 'src_rank', 'phase', 'token_index', 'layer_id', 'expert_ids'},
                'Unsupported Token fields')
        require(isinstance(token['request_id'], str) and bool(token['request_id']), 'Invalid request_id')
        integer(token['src_rank'], 'Token src_rank', 0, 7)
        integer(token['token_index'], 'Token token_index')
        require(token['phase'] == 'decode' and token['layer_id'] == c['layer_id'], 'Token phase/layer mismatch')
        experts = token['expert_ids']
        require(isinstance(experts, list) and len(experts) == c['top_k'], 'Missing or wrong Top-K route')
        for expert in experts:
            integer(expert, 'expert_id', 0, len(placement) - 1)
        require(len(set(experts)) == len(experts), 'Duplicate expert choice')
        identity = (token['request_id'], token['phase'], token['token_index'], token['layer_id'])
        require(identity not in identities, 'Duplicate Token identity')
        identities.add(identity)
        ranks.append(token['src_rank'])
    require(sorted(ranks) == c['rank_ids'], 'M2.1 requires one local Token per rank')


def build_plan(config, identities=None):
    validate_config(config)
    c = deepcopy(config)
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
    require(all(expert_inputs.values()), 'M2.1 requires positive expert work on every rank')
    transfers = []
    for op in operations:
        matrix = remote if op['kind'] == 'dispatch' else {(dst, src): v for (src, dst), v in remote.items()}
        for (src, dst), sources in sorted(matrix.items()):
            body = len(sources) * c['hidden_dim'] * c[op['kind'] + '_element_bytes']
            integer(body, 'directed vector bytes', 1, MAX_TIME_NS)
            count = (body + c['chunk_bytes'] - 1) // c['chunk_bytes']
            require(len(transfers) + count <= MAX_TRANSFERS, 'Functional plan exceeds 4096 transfers')
            for index, offset in enumerate(range(0, body, c['chunk_bytes'])):
                size = min(c['chunk_bytes'], body - offset)
                ordinal = len(transfers)
                transfers.append(dict(transfer_id=2**32 + 100 + ordinal, ub_task_id=100 + ordinal,
                    comm_tag=100 + ordinal, operation_id=op['operation_id'], kind=op['kind'],
                    src_rank=src, dst_rank=dst, payload_bytes=size, business_payload_bytes=size,
                    application_metadata_bytes=0, chunk_index=index, task_byte_range=[0, size],
                    pair_byte_range=[offset, offset + size], pair_payload_bytes=body,
                    transport_profile_id='urma-write-rtp-default', priority=7, sources=sources))
    rank_graphs = []
    for rank in c['rank_ids']:
        nodes = []
        def comp(name, duration, deps, inputs=None):
            node = dict(node_id=len(nodes) + 1, rank_id=rank, kind='COMP', name=name,
                        duration_ns=duration, resource_id='gpu_compute', deps=list(deps))
            if inputs is not None:
                node['expert_inputs'] = inputs
            nodes.append(node)
            return node['node_id']
        def communication(kind, predecessor):
            ids = []
            for direction in ('SEND', 'RECV'):
                mine = sorted((t for t in transfers if t['kind'] == kind and
                               t['src_rank' if direction == 'SEND' else 'dst_rank'] == rank),
                              key=lambda t: (t['dst_rank' if direction == 'SEND' else 'src_rank'], t['chunk_index']))
                for t in mine:
                    node_id = len(nodes) + 1
                    nodes.append(dict(node_id=node_id, rank_id=rank, kind=direction,
                        name=f"{kind}-{direction.lower()}-{t['src_rank']}-{t['dst_rank']}",
                        transfer_id=t['transfer_id'], deps=[predecessor]))
                    t['send_node_id' if direction == 'SEND' else 'recv_node_id'] = node_id
                    ids.append(node_id)
            return ids
        profile = c['compute_profile_ns']
        prepare = comp('attention-route-pack', profile['attention_route_pack'], [])
        dispatch = communication('dispatch', prepare)
        expert = comp('expert-compute', len(expert_inputs[rank]) * profile['expert_per_input'],
                      [prepare] + dispatch, expert_inputs[rank])
        local_merge = comp('expert-local-merge', profile['expert_local_merge'], [expert])
        combine = communication('combine', local_merge)
        finish = comp('combine-unpack-merge', profile['combine_unpack_merge'], [local_merge] + combine)
        next_layer = comp('next-layer', profile['next_layer'], [finish])
        rank_graphs.append(dict(rank_id=rank, nodes=nodes, phase_nodes=dict(prepare=prepare,
            expert=expert, local_merge=local_merge, finish=finish, next_layer=next_layer)))
    plan = dict(schema_version='moe-ub-execution-plan/1', graph_id=c['graph_id'], wave_id=c['wave_id'],
        rank_ids=c['rank_ids'], rank_to_ub_node=c['rank_to_ub_node'], ready_ns=c['ready_ns'],
        instance_batches=[dict(rank_id=t['src_rank'], request_ids=[t['request_id']]) for t in c['tokens']],
        communication_template=c['template'], communication_profile_basis='reference_model',
        compute_source='synthetic_fixed_duration', route_source='explicit_synthetic_fixture',
        calibration_status='unvalidated_reference', sender_completion='transaction_done',
        send_slots_per_rank=c['send_slots_per_rank'], operations=operations, transfers=transfers, rank_graphs=rank_graphs,
        expert_input_counts=[len(v) for v in expert_inputs.values()], input=c)
    if identities is not None:
        identities.assign(plan)
    validate_plan(plan)
    return plan


def validate_plan(plan):
    require(plan['schema_version'] == 'moe-ub-execution-plan/1', 'Unsupported plan schema')
    ranks = plan['rank_ids']
    require(ranks == plan['input']['rank_ids'] and plan['rank_to_ub_node'] == ranks, 'Invalid rank mapping')
    integer(plan['send_slots_per_rank'], 'send_slots_per_rank', 1, 2)
    validate_config(plan['input'])
    require(plan['send_slots_per_rank'] == plan['input']['send_slots_per_rank'], 'Send slot configuration mismatch')
    operations = {op['operation_id']: op for op in plan['operations']}
    require(len(operations) == len(plan['operations']) == 2, 'Duplicate or missing operation')
    for op in operations.values():
        require(op['participants'] == ranks, 'Operation participants mismatch')
    transfers = {t['transfer_id']: t for t in plan['transfers']}
    require(0 < len(transfers) == len(plan['transfers']) <= MAX_TRANSFERS, 'Duplicate/missing/too many transfers')
    for field in ('comm_tag', 'ub_task_id'):
        require(len({t[field] for t in transfers.values()}) == len(transfers), f'Duplicate {field}')
    for t in transfers.values():
        integer(t['transfer_id'], 'transfer_id', 1, 2**64 - 1)
        integer(t['ub_task_id'], 'ub_task_id', 0, 2**32 - 1)
        # The locked ASTRA NATIVE path remaps tags >= 500000000; do not truncate.
        integer(t['comm_tag'], 'comm_tag', 1, 499999999)
        integer(t['payload_bytes'], 'payload_bytes', 1, MAX_TASK_BYTES)
        require(t['src_rank'] in ranks and t['dst_rank'] in ranks and t['src_rank'] != t['dst_rank'], 'Invalid transfer endpoints')
        require(t['operation_id'] in operations and t['kind'] == operations[t['operation_id']]['kind'], 'Unknown operation')
        require(t['business_payload_bytes'] == t['payload_bytes'] and t['application_metadata_bytes'] == 0 and
                t['task_byte_range'] == [0, t['payload_bytes']] and
                t['priority'] == 7 and t['transport_profile_id'] == 'urma-write-rtp-default', 'Invalid payload/profile')
    grouped = defaultdict(list)
    for t in transfers.values():
        integer(t['chunk_index'], 'chunk_index', 0, MAX_TRANSFERS - 1)
        grouped[t['operation_id'], t['src_rank'], t['dst_rank']].append(t)
    for parts in grouped.values():
        first = parts[0]
        expected = len(first['sources']) * plan['input']['hidden_dim'] * plan['input'][first['kind'] + '_element_bytes']
        offset = 0
        for index, t in enumerate(parts):
            size = min(plan['input']['chunk_bytes'], expected - offset)
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


class RunIdentities:
    """Allocate bounded run identities atomically; local graph node ids stay local."""
    def __init__(self, transfer=2**32 + 100, task=100, tag=100):
        self.transfer, self.task, self.tag = transfer, task, tag
        self.graphs, self.operations = set(), set()

    def assign(self, plan):
        count = len(plan['transfers'])
        operations = {op['operation_id'] for op in plan['operations']}
        require(plan['graph_id'] not in self.graphs, 'Reused graph_id')
        require(not self.operations & operations, 'Reused operation_id')
        for start, maximum, name in ((self.transfer, 2**64 - 1, 'transfer_id'),
                                      (self.task, 2**32 - 1, 'ub_task_id'),
                                      (self.tag, 499999999, 'comm_tag')):
            integer(start, name, 1, maximum)
            require(count <= maximum - start + 1, name + ' allocator exhausted')
        mapping = {}
        for index, transfer in enumerate(plan['transfers']):
            mapping[transfer['transfer_id']] = self.transfer + index
            transfer.update(transfer_id=self.transfer + index, ub_task_id=self.task + index,
                            comm_tag=self.tag + index)
        for graph in plan['rank_graphs']:
            for node in graph['nodes']:
                if 'transfer_id' in node:
                    node['transfer_id'] = mapping[node['transfer_id']]
        self.transfer += count; self.task += count; self.tag += count
        self.graphs.add(plan['graph_id']); self.operations.update(operations)
