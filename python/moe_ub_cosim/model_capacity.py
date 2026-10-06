"""Explicit bias-free GQA decoder inventory; capacities belong to rank endpoints.

This is analytical sizing of a declared structure, not an HF architecture importer
or hardware calibration. Dense and routed/shared FFNs use three gated matrices.
"""
from copy import deepcopy

from .plan import batch_limit, integer, require

I64 = 2**63 - 1
DTYPE_BYTES = {'float16': 2, 'bfloat16': 2, 'float32': 4}


def fields(value, expected, name):
    require(isinstance(value, dict) and set(value) == set(expected.split()), 'Invalid ' + name + ' fields')


def audit_capacity(model, memory, layer):
    if isinstance(model, dict) and model.get('schema_version') == 'moe-ub-model/2':
        from .mla_capacity import audit_execution_capacity
        return audit_execution_capacity(model, memory, layer)
    fields(model, 'schema_version name architecture source hidden_size vocab_size max_position_embeddings '
           'tp pp attention dtypes tie_word_embeddings layers', 'model')
    require(model['schema_version'] == 'moe-ub-model/1' and
            model['architecture'] == 'bias_free_gqa_rmsnorm_gated_ffn', 'Unsupported model architecture/schema')
    require(isinstance(model['name'], str) and bool(model['name'].strip()), 'Missing model name')
    fields(model['source'], 'kind description', 'model source')
    require(model['source']['kind'] in ('synthetic', 'declared_reference') and
            isinstance(model['source']['description'], str) and model['source']['description'].strip(),
            'Model source must state its synthetic or declared reference basis')
    for key in ('tp', 'pp'):
        integer(model[key], key, 1, 1)
    h = integer(model['hidden_size'], 'hidden_size', 1, I64)
    v = integer(model['vocab_size'], 'vocab_size', 1, I64)
    integer(model['max_position_embeddings'], 'max_position_embeddings', 1, I64)
    require(type(model['tie_word_embeddings']) is bool, 'Invalid tied weights flag')
    a = model['attention']
    fields(a, 'num_heads num_kv_heads head_dim', 'attention')
    q = integer(a['num_heads'], 'num_heads', 1, I64)
    k = integer(a['num_kv_heads'], 'num_kv_heads', 1, q)
    d = integer(a['head_dim'], 'head_dim', 1, I64)
    require(q % k == 0 and q*d == h, 'Invalid GQA dimensions')
    dt = model['dtypes']
    vector_bytes = dict(DTYPE_BYTES, **({'int8':1} if layer['template'] in ('cann_a3_v850_fullmesh_v1','cann_a3_v850_native_v1') else {}))
    fields(dt, 'weights compute kv dispatch combine', 'dtypes')
    require(all(isinstance(value, str) for value in dt.values()) and
            all(dt[key] in DTYPE_BYTES for key in ('weights', 'compute', 'combine')) and dt['dispatch'] in vector_bytes and
            dt['kv'] in dict(DTYPE_BYTES, fp8=1), 'Unsupported model dtype')
    w = DTYPE_BYTES[dt['weights']]
    kv_bytes = dict(DTYPE_BYTES, fp8=1)[dt['kv']]
    require(h == layer['hidden_dim'] and
            vector_bytes[dt['dispatch']] == layer['dispatch_element_bytes'] and
            DTYPE_BYTES[dt['combine']] == layer['combine_element_bytes'], 'Model and execution dimensions/dtypes differ')
    ranks = layer['rank_ids']
    layers = model['layers']
    require(isinstance(layers, list) and layers, 'Model needs a nonempty layer inventory')
    shared = dict(embedding=v*h*w, final_norm=h*w,
                  lm_head=0 if model['tie_word_embeddings'] else v*h*w)
    per_rank = {r: [] for r in ranks}
    for index, row in enumerate(layers):
        require(isinstance(row, dict) and row.get('kind') in ('dense', 'moe'), 'Unsupported layer kind')
        common = 'layer_id kind intermediate_size'
        fields(row, common if row['kind'] == 'dense' else
               common + ' num_experts top_k expert_placement shared_experts shared_intermediate_size', 'layer')
        integer(row['layer_id'], 'layer_id', index, index)
        f = integer(row['intermediate_size'], 'intermediate_size', 1, I64)
        base = dict(norms=2*h*w, attention_qkv=h*(q+2*k)*d*w, attention_o=q*d*h*w)
        if row['kind'] == 'dense':
            base['dense_ffn'] = 3*h*f*w
        else:
            experts = integer(row['num_experts'], 'num_experts', 1, I64)
            integer(row['top_k'], 'top_k', 1, experts)
            placement = row['expert_placement']
            require(isinstance(placement, list) and len(placement) == experts and
                    all(type(r) is int and r in per_rank for r in placement), 'Invalid model expert placement')
            s = integer(row['shared_experts'], 'shared_experts', 0, I64)
            sf = integer(row['shared_intermediate_size'], 'shared_intermediate_size', 0, I64)
            require((s == 0) == (sf == 0), 'Shared expert count/dimension mismatch')
            base.update(router=h*experts*w, shared_experts=3*h*sf*s*w)
        for rank in ranks:
            components = deepcopy(base)
            count = row['expert_placement'].count(rank) if row['kind'] == 'moe' else 0
            if row['kind'] == 'moe':
                components['routed_experts'] = count*3*h*f*w
            per_rank[rank].append(dict(layer_id=index, kind=row['kind'], local_experts=count,
                                       components=components, weight_bytes=sum(components.values())))
    selected = integer(layer['layer_id'], 'executed layer_id', 0, len(layers)-1)
    row = layers[selected]
    require(row['kind'] == 'moe', 'Selected execution layer must be MoE; dense layers are not converted')
    require(row['shared_experts'] == 0, 'Selected shared-expert execution is not implemented')
    require(row['top_k'] == layer['top_k'] and row['expert_placement'] == layer['expert_placement'],
            'Selected layer placement/Top-K mismatch')
    require(layer['role'] == 'D' and layer['compute_profile_ns']['next_layer'] == 0,
            'Selected-layer decode must omit synthetic next_layer compute')

    fields(memory, 'capacity_scope block_size max_kv_blocks_per_rank ranks', 'memory')
    require(memory['capacity_scope'] == 'rank_endpoint', 'Capacity must belong to an independent rank endpoint')
    block_size = integer(memory['block_size'], 'block_size', 1, I64)
    max_blocks = integer(memory['max_kv_blocks_per_rank'], 'max_kv_blocks_per_rank', 1, I64)
    budgets = memory['ranks']
    require(isinstance(budgets, list) and len(budgets) == len(ranks), 'Missing rank memory budgets')
    bpt = 2*k*d*len(layers)*kv_bytes  # EP partitions experts, never this request's KV.
    block_bytes = integer(bpt*block_size, 'kv_block_bytes', 1, I64)
    minimum_buffer = communication_minimum(layer)
    audit = []
    for rank, budget in zip(ranks, budgets):
        fields(budget, 'rank_id device_bytes workspace_bytes communication_buffer_bytes', 'rank memory')
        integer(budget['rank_id'], 'memory rank_id', rank, rank)
        for key in ('device_bytes', 'workspace_bytes', 'communication_buffer_bytes'):
            integer(budget[key], key, 0 if key != 'device_bytes' else 1, I64)
        require(budget['communication_buffer_bytes'] >= minimum_buffer, 'Communication buffer below graph bound')
        weights = integer(sum(shared.values())+sum(r['weight_bytes'] for r in per_rank[rank]),
                          'rank weight_bytes', 1, I64)
        non_kv = weights + budget['workspace_bytes'] + budget['communication_buffer_bytes']
        available = budget['device_bytes'] - non_kv
        require(available >= block_bytes, f'Rank {rank}: weights/workspace/buffers leave less than one KV block')
        capacity = available // block_bytes
        require(capacity <= max_blocks, f'Rank {rank}: KV pool exceeds configured host block budget; no truncation')
        audit.append(dict(**deepcopy(budget), shared_components=deepcopy(shared), layers=per_rank[rank],
                          weight_bytes=weights, non_kv_bytes=non_kv, kv_bytes_per_token=bpt,
                          kv_block_bytes=block_bytes, kv_capacity_blocks=capacity,
                          unallocated_bytes=available % block_bytes))
    return dict(schema_version='moe-ub-configured-model-audit/1', model=deepcopy(model),
                execution_scope='single_selected_moe_layer', executed_layer_ids=[selected],
                unexecuted_layer_ids=[i for i in range(len(layers)) if i != selected],
                unexecuted_components=['embedding', 'final_norm', 'lm_head', 'sampling'],
                communication_buffer_minimum_bytes=minimum_buffer, ranks=audit,
                memory_basis='analytical_declared_structure_and_static_placement',
                full_model_execution_validated=False, target_deployment_validated=False)


def communication_minimum(layer):
    """Retain remote vectors and configured slots even for all-local routes."""
    ranks = layer['rank_ids']
    remote = min(layer['top_k'], len(ranks)-1)
    batch = batch_limit(layer) if layer['schema_version'] == 'moe-ub-layer-fixture/3' else 1
    minimum = max(batch*len(ranks)*remote*layer['hidden_dim']*
                  (layer['dispatch_element_bytes']+layer['combine_element_bytes']),
                  2*layer['send_slots_per_rank']*layer['chunk_bytes'])
    if layer['template'] in ('cann_a3_v850_fullmesh_v1','cann_a3_v850_native_v1'):
        from .cann import buffers
        minimum = max(minimum, buffers(layer)['total_bytes'])
    return minimum
