"""Platform native operations over the source-derived CANN message layout.

This mapping is a platform model, not a claim that CANN executes these ns-3
operations. A LOAD is preceded by a real metadata STORE from its data owner.
SEND denotes request initiation; for LOAD its peer RECV releases the owner's
buffer. The consumer continues from LOAD SEND completion after all responses.
"""
from copy import deepcopy

from .plan import integer, require
from .locality import LOCAL_PROFILE

TEMPLATE = 'cann_a3_v850_native_v1'
STORE = 'native-store-default'
LOAD = 'native-load-default'


def validate_profile(profile):
    require(isinstance(profile, dict) and set(profile) == {'dispatch', 'combine', 'ready_notice_bytes'},
            'Invalid native profile fields')
    require(all(profile[k] in ('STORE', 'LOAD') for k in ('dispatch', 'combine')),
            'Native vector operations must be STORE or LOAD')
    require(profile['ready_notice_bytes'] == 32 and type(profile['ready_notice_bytes']) is int,
            'Native ready notification is one declared 32-byte metadata STORE')


def apply_mapping(base, config):
    p = deepcopy(base)
    profile = config['native_profile']
    validate_profile(profile)
    p['input'] = deepcopy(config)
    p['communication_template'] = TEMPLATE
    p['communication_evidence']['transaction_mapping'] = 'platform_native_store_load_not_native_MTE'
    p['native_mapping'] = dict(data_layout='cann_a3_v850_fullmesh_v1',
        status_operation='STORE', load_readiness='metadata_STORE_before_consumer_LOAD',
        graph_send='request_initiation', graph_recv='peer_wait',
        load_consumer_dependency='SEND_transaction_done_after_all_response_data',
        hardware_calibrated=False)
    original = {t['transfer_id']: deepcopy(t) for t in p['transfers']}
    notices = {}
    next_transfer = max(original, default=0)+1
    next_task = max((t.get('ub_task_id', 0) for t in p['transfers']), default=0)+1
    next_tag = max((t['comm_tag'] for t in p['transfers']), default=0)+1
    next_message = max((t['message_id'] for t in p['transfers']), default=0)+1
    for t in p['transfers']:
        t['send_node_id'] *= 2
        t['recv_node_id'] *= 2
        if t['transport_profile_id'] == LOCAL_PROFILE:
            continue
        operation = profile[t['kind']] if t['message_role'] == 'data' else 'STORE'
        t.update(operation=operation, data_owner_rank=t['src_rank'], consumer_rank=t['dst_rank'])
        t['transport_profile_id'] = STORE if operation == 'STORE' else LOAD
        if operation == 'LOAD':
            notice = deepcopy(t)
            notice.update(transfer_id=next_transfer, ub_task_id=next_task, comm_tag=next_tag,
                message_id=next_message, message_role='ready', ready_for_message_id=t['message_id'],
                operation='STORE', transport_profile_id=STORE, payload_bytes=32,
                business_payload_bytes=0, application_metadata_bytes=32, padding_bytes=0,
                payload_regions=[dict(begin=0, end=32, kind='metadata')], task_byte_range=[0,32],
                pair_byte_range=[0,32], pair_payload_bytes=32, sources=[],
                send_node_id=t['send_node_id']-1, recv_node_id=t['recv_node_id']-1)
            notices[t['transfer_id']] = notice
            next_transfer += 1; next_task += 1; next_tag += 1; next_message += 1
            t['src_rank'], t['dst_rank'] = t['dst_rank'], t['src_rank']
            t['send_node_id'], t['recv_node_id'] = t['recv_node_id'], t['send_node_id']
        t['initiator_rank'] = t['src_rank']
    for notice in notices.values():
        notice['initiator_rank'] = notice['src_rank']
    p['transfers'].extend(notices.values())
    for graph in p['rank_graphs']:
        nodes = []
        for old in graph['nodes']:
            n = deepcopy(old)
            n['layout_node_id'] = old['node_id']
            n['node_id'] *= 2
            n['deps'] = [2*d for d in n['deps']]
            tid = n.get('transfer_id')
            if tid in notices:
                notice = deepcopy(n)
                notice.update(node_id=n['node_id']-1, transfer_id=notices[tid]['transfer_id'],
                              name=n['name']+'-ready-notice')
                nodes.append(notice)
                n.update(kind='RECV' if old['kind']=='SEND' else 'SEND',
                         deps=[notice['node_id']], name=n['name']+'-native-load')
            nodes.append(n)
        graph['nodes'] = nodes
        for field in ('phase_nodes', 'local_prepare_nodes'):
            if field in graph:
                graph[field] = {k: None if v is None else 2*v for k,v in graph[field].items()}
        dense = {n['node_id']:i+1 for i,n in enumerate(nodes)}
        for n in nodes:
            n['node_id'] = dense[n['node_id']]
            n['deps'] = [dense[d] for d in n['deps']]
        for field in ('phase_nodes', 'local_prepare_nodes'):
            if field in graph:
                graph[field] = {k:None if v is None else dense[v] for k,v in graph[field].items()}
        for t in p['transfers']:
            if t['src_rank'] == graph['rank_id']:
                t['send_node_id'] = dense[t['send_node_id']]
            if t['dst_rank'] == graph['rank_id']:
                t['recv_node_id'] = dense[t['recv_node_id']]
    from .resources import actual_usage
    if 'resource_budget' in config:
        p['resource_usage'] = actual_usage(p)
    return p


def build_plan(config, identities=None, *, _validate=True):
    from .cann import build_plan as build_cann, TEMPLATE as CANN
    from .resources import cann_usage, check_graph, actual_usage
    validate_profile(config['native_profile'])
    usage = check_graph(cann_usage(config), config.get('resource_budget'))
    base_config = deepcopy(config)
    base_config['template'] = CANN
    del base_config['native_profile']
    p = apply_mapping(build_cann(base_config), config)
    require(actual_usage(p) == usage, 'Native graph preflight mismatch')
    if identities is not None:
        identities.assign(p)
    if _validate:
        validate_plan(p)
    return p


def validate_plan(plan):
    from .plan import validate_config
    validate_config(plan['input'])
    expected = build_plan(plan['input'], _validate=False)
    require(len(plan['transfers']) == len(expected['transfers']), 'Native transfer count mismatch')
    mapping = {}; seen = {k:set() for k in ('transfer_id','comm_tag','ub_task_id')}
    for actual, canonical in zip(plan['transfers'], expected['transfers']):
        mapping[canonical['transfer_id']] = actual['transfer_id']
        for key, limit in (('transfer_id',2**64-1),('comm_tag',499999999),('ub_task_id',2**32-1)):
            if key in canonical:
                integer(actual[key], key, 0 if key=='ub_task_id' else 1, limit)
                require(actual[key] not in seen[key], 'Native reused '+key)
                seen[key].add(actual[key]); canonical[key] = actual[key]
    for graph in expected['rank_graphs']:
        for n in graph['nodes']:
            if 'transfer_id' in n:
                n['transfer_id'] = mapping[n['transfer_id']]
    for key, value in expected.items():
        if key not in ('route_source','compute_source'):
            require(plan.get(key) == value, 'Native canonical mismatch: '+key)
