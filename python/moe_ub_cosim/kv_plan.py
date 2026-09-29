"""Bounded MHA K/V handoff: compatible TP=1 layouts, one request, no Q/padding."""
from copy import deepcopy

from .plan import integer, require, MAX_TASK_BYTES, MAX_TRANSFERS


def validate_kv_input(c):
    require(set(c) == {'schema_version', 'graph_id', 'wave_id', 'ready_ns', 'service_id',
        'request_id', 'source_group', 'destination_group', 'src_rank', 'dst_rank', 'model',
        'token_range', 'source_layout', 'destination_layout', 'kv_dtype', 'chunk_bytes',
        'send_slots_per_rank', 'kv_handoff_policy'}, 'Unsupported KV fields')
    require(c['schema_version'] == 'moe-ub-kv-fixture/1' and
        c['kv_handoff_policy'] == 'between_iterations', 'Unsupported KV schema/policy')
    for key in ('graph_id', 'wave_id', 'service_id', 'request_id'):
        require(isinstance(c[key], str) and bool(c[key]), 'Invalid KV ' + key)
    integer(c['ready_ns'], 'ready_ns', 0, 2**63 - 1000001)
    require(c['source_layout'] == c['destination_layout'] == 'layer-kv-token-head-dim-tp1',
            'Incompatible KV layouts; conversion is not implemented')
    require(c['kv_dtype'] in ('bfloat16', 'fp8'), 'Unsupported KV dtype')
    model = c['model']
    require(set(model) == {'cache_format', 'num_hidden_layers', 'num_attention_heads',
        'num_key_value_heads', 'head_dim'}, 'Invalid KV model fields')
    require(model['cache_format'] == 'MHA', 'Only explicit MHA KV is implemented')
    integer(model['num_hidden_layers'], 'KV layers', 1, 1)
    integer(model['num_attention_heads'], 'attention heads', 1, 32)
    require(type(model['num_key_value_heads']) is int and model['num_key_value_heads'] ==
        model['num_attention_heads'], 'MHA KV heads must equal attention heads')
    integer(model['head_dim'], 'head_dim', 1, 256)
    require(isinstance(c['token_range'], list) and len(c['token_range']) == 2 and
        type(c['token_range'][0]) is int and c['token_range'][0] == 0, 'KV range must begin at zero')
    integer(c['token_range'][1], 'KV token count', 1, 16)
    members = []
    for field, endpoint in (('source_group', 'src_rank'), ('destination_group', 'dst_rank')):
        group = c[field]
        require(set(group) == {'group_id', 'rank_ids'}, 'Invalid KV group')
        require(isinstance(group['group_id'], str) and bool(group['group_id']), 'Invalid KV group identity')
        ranks = group['rank_ids']
        require(isinstance(ranks, list) and len(ranks) == 4 and
            all(type(r) is int and 0 <= r < 8 for r in ranks) and ranks == sorted(set(ranks)), 'Invalid KV members')
        integer(c[endpoint], endpoint, 0, 7)
        require(c[endpoint] in ranks, 'KV endpoint outside its group')
        members.extend(ranks)
    require(sorted(members) == list(range(8)) and c['source_group']['group_id'] !=
        c['destination_group']['group_id'], 'KV groups overlap or have repeated identity')
    integer(c['chunk_bytes'], 'KV chunk_bytes', 1, MAX_TASK_BYTES)
    integer(c['send_slots_per_rank'], 'KV send slots', 1, 2)


def build_kv_plan(config, identities=None):
    validate_kv_input(config)
    c = deepcopy(config)
    model, tokens = c['model'], c['token_range'][1]
    element = 2 if c['kv_dtype'] == 'bfloat16' else 1
    size = tokens * model['num_key_value_heads'] * model['head_dim'] * element
    ranks = list(range(8))
    operation = dict(operation_id=c['wave_id'] + '.KV_TRANSFER', kind='KV_TRANSFER',
        service_id=c['service_id'], role='P/D', wave_id=c['wave_id'], layer_id=0,
        group_id=c['source_group']['group_id'] + '->' + c['destination_group']['group_id'], participants=ranks)
    transfers = []
    for component_index, component in enumerate(('K', 'V')):
        source = dict(request_id=c['request_id'], component=component, layer_id=0,
            token_range=c['token_range'], head_range=[0, model['num_key_value_heads']],
            head_dim=model['head_dim'], kv_dtype=c['kv_dtype'], layout=c['source_layout'])
        for offset in range(0, size, c['chunk_bytes']):
            ordinal = len(transfers)
            require(ordinal < MAX_TRANSFERS, 'KV plan exceeds transfer bound')
            count = min(size-offset, c['chunk_bytes'])
            transfers.append(dict(transfer_id=2**32+100+ordinal, ub_task_id=100+ordinal, comm_tag=100+ordinal,
                operation_id=operation['operation_id'], kind='KV_TRANSFER', src_rank=c['src_rank'], dst_rank=c['dst_rank'],
                payload_bytes=count, business_payload_bytes=count, application_metadata_bytes=0,
                chunk_index=ordinal, task_byte_range=[0,count], pair_byte_range=[component_index*size+offset,component_index*size+offset+count],
                pair_payload_bytes=2*size, component_byte_range=[offset,offset+count], kv_component=component, transport_profile_id='urma-write-rtp-default',
                priority=7, sources=[source], send_node_id=ordinal+1, recv_node_id=ordinal+1))
    graphs = []
    for rank in ranks:
        nodes = []
        if rank in (c['src_rank'], c['dst_rank']):
            side = 'SEND' if rank == c['src_rank'] else 'RECV'
            nodes = [dict(node_id=i+1, rank_id=rank, kind=side, name=f'kv-{t["kv_component"]}-{side.lower()}-{i}',
                          transfer_id=t['transfer_id'], deps=[]) for i,t in enumerate(transfers)]
        graphs.append(dict(rank_id=rank, nodes=nodes))
    plan = dict(schema_version='moe-ub-execution-plan/1', graph_id=c['graph_id'], wave_id=c['wave_id'],
        ready_ns=c['ready_ns'], rank_ids=ranks, rank_to_ub_node=ranks,
        instance_batches=[dict(rank_id=r, request_ids=[c['request_id']] if r in (c['src_rank'],c['dst_rank']) else []) for r in ranks],
        communication_template='kv_direct_v1', communication_profile_basis='reference_model',
        compute_source='no_compute', route_source='explicit_kv_mapping', calibration_status='unvalidated_reference',
        sender_completion='transaction_done', send_slots_per_rank=c['send_slots_per_rank'],
        operations=[operation], transfers=transfers, rank_graphs=graphs, kv_payload_bytes=2*size, input=c)
    if identities is not None: identities.assign(plan)
    return plan


def validate_kv_plan(plan):
    expected = build_kv_plan(plan['input'])
    require(len(plan['transfers']) == len(expected['transfers']), 'KV transfer count mismatch')
    mapping = {}
    for field, limit in (('transfer_id', 2**64-1), ('ub_task_id', 2**32-1), ('comm_tag', 499999999)):
        values = [integer(t[field], field, 1, limit) for t in plan['transfers']]
        require(len(values) == len(set(values)), 'Repeated KV ' + field)
    for reference, actual in zip(expected['transfers'], plan['transfers']):
        mapping[reference['transfer_id']] = actual['transfer_id']
        for key in ('transfer_id', 'ub_task_id', 'comm_tag'): reference[key] = actual[key]
    for graph in expected['rank_graphs']:
        for node in graph['nodes']: node['transfer_id'] = mapping[node['transfer_id']]
    require(plan == expected, 'KV plan differs from model, mapping, byte ranges or graph')
