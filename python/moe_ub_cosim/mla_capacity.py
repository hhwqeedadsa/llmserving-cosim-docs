"""MLA model/2 storage inventory, independent of model/1 GQA and execution.

Matrices are output x input. INT8 is a storage assumption, never a kernel claim.
EP shards routed experts only; compressed request KV and other weights replicate.
"""
from copy import deepcopy

from .model_capacity import DTYPE_BYTES, I64, communication_minimum, fields
from .plan import integer, require

MATRIX_GROUPS = ('mla', 'dense_ffn', 'routed_experts', 'shared_experts')
STORAGE_GROUPS = MATRIX_GROUPS + ('embedding', 'lm_head', 'norm', 'router', 'router_correction')


def validate_model(model, ranks):
    fields(model, 'schema_version name architecture source hidden_size vocab_size max_position_embeddings '
           'tp pp attention dtypes tie_word_embeddings layers weight_storage kv_cache', 'MLA model')
    require(model['schema_version'] == 'moe-ub-model/2' and
            model['architecture'] == 'bias_free_mla_rmsnorm_gated_ffn', 'Unsupported MLA architecture/schema')
    require(isinstance(model['name'], str) and model['name'].strip(), 'Missing model name')
    fields(model['source'], 'kind description', 'model source')
    require(model['source']['kind'] in ('synthetic', 'declared_reference') and
            isinstance(model['source']['description'], str) and model['source']['description'].strip(),
            'Model source must state its synthetic or declared reference basis')
    require(isinstance(ranks, list) and ranks and all(type(r) is int and 0 <= r <= 2**31-2 for r in ranks)
            and len(set(ranks)) == len(ranks), 'Invalid or duplicate capacity ranks')
    for key in ('tp', 'pp'):
        integer(model[key], key, 1, 1)
    for key in ('hidden_size', 'vocab_size', 'max_position_embeddings'):
        integer(model[key], key, 1, I64)
    require(type(model['tie_word_embeddings']) is bool, 'Invalid tied weights flag')
    a = model['attention']
    fields(a, 'num_heads q_lora_rank kv_lora_rank qk_nope_head_dim qk_rope_head_dim v_head_dim', 'MLA attention')
    for key, value in a.items():
        integer(value, key, 1, I64)
    require(a['qk_rope_head_dim'] % 2 == 0, 'RoPE dimension must be even')
    dt = model['dtypes']
    fields(dt, 'compute kv dispatch combine', 'MLA dtypes')
    require(all(isinstance(v, str) for v in dt.values()) and
            all(dt[k] in DTYPE_BYTES for k in ('compute', 'kv', 'combine')) and
            dt['dispatch'] in dict(DTYPE_BYTES, int8=1), 'Unsupported MLA dtype')
    cache = model['kv_cache']
    fields(cache, 'layout quantization padding_bytes_per_token', 'MLA KV cache')
    require(cache['layout'] == 'absorbed_latent_rope' and cache['quantization'] == 'none',
            'Unsupported MLA KV layout/quantization')
    integer(cache['padding_bytes_per_token'], 'KV padding', 0, 0)
    storage = model['weight_storage']
    fields(storage, ' '.join(STORAGE_GROUPS), 'MLA component storage')
    for name, policy in storage.items():
        fields(policy, 'dtype quantization scale_dtype zero_point padding_bytes', name + ' storage')
        integer(policy['zero_point'], 'storage zero_point', 0, 0)
        integer(policy['padding_bytes'], 'storage padding_bytes', 0, 0)
        dtype = policy['dtype']
        require(isinstance(dtype, str), 'Invalid weight dtype')
        if dtype == 'int8':
            require(name in MATRIX_GROUPS and policy['quantization'] == 'symmetric_per_output_channel' and
                    policy['scale_dtype'] == 'float32', 'INT8 requires matrix output-channel FP32 scales')
        else:
            require(dtype in DTYPE_BYTES and policy['quantization'] == 'none' and policy['scale_dtype'] is None,
                    'Floating storage requires no quantization/scales')
    require(not model['tie_word_embeddings'] or storage['embedding'] == storage['lm_head'],
            'Tied embedding/head must have identical storage')
    layers = model['layers']
    require(isinstance(layers, list) and layers, 'Model needs a nonempty layer inventory')
    for index, row in enumerate(layers):
        require(isinstance(row, dict) and row.get('kind') in ('dense', 'moe'), 'Unsupported layer kind')
        common = 'layer_id kind intermediate_size'
        fields(row, common if row['kind'] == 'dense' else common +
               ' num_experts top_k expert_placement shared_experts shared_intermediate_size router_correction_bias', 'MLA layer')
        integer(row['layer_id'], 'layer_id', index, index)
        integer(row['intermediate_size'], 'intermediate_size', 1, I64)
        if row['kind'] == 'dense':
            continue
        e = integer(row['num_experts'], 'num_experts', 1, I64)
        integer(row['top_k'], 'top_k', 1, e)
        placement = row['expert_placement']
        require(isinstance(placement, list) and len(placement) == e and
                all(type(r) is int and r in ranks for r in placement), 'Invalid model expert placement')
        s = integer(row['shared_experts'], 'shared_experts', 0, I64)
        sf = integer(row['shared_intermediate_size'], 'shared_intermediate_size', 0, I64)
        require((s == 0) == (sf == 0), 'Shared expert count/dimension mismatch')
        require(type(row['router_correction_bias']) is bool, 'Invalid router correction flag')


def inventory(model, ranks):
    """Return aggregate tensor records; count represents independent copies."""
    validate_model(model, ranks)
    h, v = model['hidden_size'], model['vocab_size']
    a = model['attention']
    n, q, c = a['num_heads'], a['q_lora_rank'], a['kv_lora_rank']
    dn, dr, dv = a['qk_nope_head_dim'], a['qk_rope_head_dim'], a['v_head_dim']
    storage = model['weight_storage']

    def tensor(name, group, shape, count=1):
        policy = storage[group]
        elements = 1
        for dimension in shape:
            elements *= integer(dimension, name + ' dimension', 1, I64)
        parameters = integer(elements*count, name + ' parameters', 0, I64)
        weight = integer(parameters*(1 if policy['dtype'] == 'int8' else DTYPE_BYTES[policy['dtype']]),
                         name + ' tensor bytes', 0, I64)
        scale = integer(shape[0]*count*4 if policy['dtype'] == 'int8' else 0, name + ' scale bytes', 0, I64)
        return dict(name=name, storage_group=group, shape=shape, count=count, parameter_count=parameters,
                    tensor_bytes=weight, scale_bytes=scale, weight_bytes=integer(weight+scale, name+' bytes', 0, I64))

    shared = [tensor('embedding', 'embedding', [v,h]), tensor('final_norm', 'norm', [h]),
              tensor('lm_head', 'lm_head', [v,h], 0 if model['tie_word_embeddings'] else 1)]
    attention = [tensor('mla_q_a', 'mla', [q,h]), tensor('mla_q_b', 'mla', [n*(dn+dr),q]),
                 tensor('mla_kv_a', 'mla', [c+dr,h]), tensor('mla_kv_b', 'mla', [n*(dn+dv),c]),
                 tensor('mla_o', 'mla', [h,n*dv]), tensor('block_norms', 'norm', [h], 2),
                 tensor('q_norm', 'norm', [q]), tensor('kv_norm', 'norm', [c])]

    def ffn(name, f, count):
        return [tensor(name+'_gate_up', name, [f,h], 2*count), tensor(name+'_down', name, [h,f], count)]

    per_rank = {}
    for rank in ranks:
        rows = []
        for row in model['layers']:
            tensors = deepcopy(attention)
            count = 0
            if row['kind'] == 'dense':
                tensors += ffn('dense_ffn', row['intermediate_size'], 1)
            else:
                e = row['num_experts']; count = row['expert_placement'].count(rank)
                tensors += [tensor('router', 'router', [e,h]),
                            tensor('router_correction', 'router_correction', [e], int(row['router_correction_bias']))]
                tensors += ffn('routed_experts', row['intermediate_size'], count)
                if row['shared_experts']:
                    tensors += ffn('shared_experts', row['shared_intermediate_size'], row['shared_experts'])
            rows.append(dict(layer_id=row['layer_id'], kind=row['kind'], local_experts=count, tensors=tensors,
                             components={t['name']:t['weight_bytes'] for t in tensors},
                             weight_bytes=sum(t['weight_bytes'] for t in tensors)))
        all_tensors = shared + [t for row in rows for t in row['tensors']]
        totals = {k:integer(sum(t[k] for t in all_tensors), 'rank '+k, 1 if k != 'scale_bytes' else 0, I64)
                  for k in ('parameter_count', 'tensor_bytes', 'scale_bytes', 'weight_bytes')}
        per_rank[rank] = dict(shared_components={t['name']:t['weight_bytes'] for t in shared},
                              shared_tensors=deepcopy(shared), layers=rows, **totals)
    bpt = integer(len(model['layers'])*(c+dr)*DTYPE_BYTES[model['dtypes']['kv']], 'MLA KV bytes/token', 1, I64)
    return per_rank, bpt


def audit_inventory(model, memory, ranks, *, communication_minimum_bytes=None):
    """Capacity-only API. A declared communication budget is not a graph proof.

The runtime adapter supplies its independently recomputed graph bound. No pool or
runtime objects are created here, so complete dense/shared inventories are legal.
"""
    per_rank, bpt = inventory(model, ranks)
    fields(memory, 'capacity_scope block_size max_kv_blocks_per_rank ranks', 'memory')
    require(memory['capacity_scope'] == 'rank_endpoint', 'Capacity must belong to an independent rank endpoint')
    block_size = integer(memory['block_size'], 'block_size', 1, I64)
    max_blocks = integer(memory['max_kv_blocks_per_rank'], 'max_kv_blocks_per_rank', 1, I64)
    block_bytes = integer(bpt*block_size, 'kv_block_bytes', 1, I64)
    budgets = memory['ranks']
    require(isinstance(budgets, list) and len(budgets) == len(ranks), 'Missing rank memory budgets')
    if communication_minimum_bytes is not None:
        integer(communication_minimum_bytes, 'communication minimum', 0, I64)
    audit = []
    for rank, budget in zip(ranks, budgets):
        fields(budget, 'rank_id device_bytes workspace_bytes communication_buffer_bytes', 'rank memory')
        integer(budget['rank_id'], 'memory rank_id', rank, rank)
        for key in ('device_bytes', 'workspace_bytes', 'communication_buffer_bytes'):
            integer(budget[key], key, 1 if key == 'device_bytes' else 0, I64)
        if communication_minimum_bytes is not None:
            require(budget['communication_buffer_bytes'] >= communication_minimum_bytes,
                    'Communication buffer below graph bound')
        row = per_rank[rank]
        non_kv = integer(row['weight_bytes'] + budget['workspace_bytes'] + budget['communication_buffer_bytes'],
                         'non KV bytes', 1, I64)
        available = budget['device_bytes'] - non_kv
        require(available >= block_bytes, f'Rank {rank}: weights/workspace/buffers leave less than one KV block')
        capacity = available // block_bytes
        require(capacity <= max_blocks, f'Rank {rank}: KV pool exceeds configured host block budget; no truncation')
        audit.append(dict(**deepcopy(budget), **row, non_kv_bytes=non_kv, kv_bytes_per_token=bpt,
                          kv_block_bytes=block_bytes, kv_capacity_blocks=capacity,
                          unallocated_bytes=available % block_bytes))
    return dict(schema_version='moe-ub-configured-model-audit/2', model=deepcopy(model),
                execution_scope='capacity_only', executed_layer_ids=[],
                unexecuted_layer_ids=[r['layer_id'] for r in model['layers']],
                unexecuted_components=['embedding', 'final_norm', 'lm_head', 'sampling'],
                communication_buffer_minimum_bytes=communication_minimum_bytes,
                communication_budget_basis=('declared_not_graph_validated' if communication_minimum_bytes is None
                                            else 'selected_execution_graph_bound'),
                ranks=audit, memory_basis='analytical_declared_structure_and_static_placement',
                kv_layout='absorbed_latent_rope', compute_validation='not_executed',
                mla_kernel_validated=False, quantized_kernels_executed=False,
                full_model_execution_validated=False, target_deployment_validated=False)


def audit_execution_capacity(model, memory, layer):
    # Validate all stored layers, including unexecuted dense/shared weights.
    audit = audit_inventory(model, memory, layer['rank_ids'],
                            communication_minimum_bytes=communication_minimum(layer))
    selected = integer(layer['layer_id'], 'executed layer_id', 0, len(model['layers'])-1)
    row = model['layers'][selected]
    require(row['kind'] == 'moe', 'Selected execution layer must be MoE; dense layers are not converted')
    require(row['shared_experts'] == 0, 'Selected shared-expert execution is not implemented')
    require('operator_profile' not in layer, 'MLA compute requires explicit reference costs; unsupported operator profile')
    require(row['top_k'] == layer['top_k'] and row['expert_placement'] == layer['expert_placement'],
            'Selected layer placement/Top-K mismatch')
    require(layer['role'] == 'D' and layer['compute_profile_ns']['next_layer'] == 0,
            'Selected-layer decode must omit synthetic next_layer compute')
    dt = model['dtypes']
    require(model['hidden_size'] == layer['hidden_dim'] and
            dict(DTYPE_BYTES, int8=1)[dt['dispatch']] == layer['dispatch_element_bytes'] and
            DTYPE_BYTES[dt['combine']] == layer['combine_element_bytes'], 'Model and execution dimensions/dtypes differ')
    require(dt['dispatch'] != 'int8' or layer['template'] == 'cann_a3_v850_fullmesh_v1',
            'INT8 dispatch requires the CANN behavior template')
    # layer/3 carries explicit compute and KV dtypes. Do not infer them from byte widths.
    require(layer['schema_version'] == 'moe-ub-layer-fixture/3' and layer['compute_dtypes'] == dt,
            'MLA reference execution requires layer/3 with matching compute dtypes')
    audit.update(execution_scope='single_selected_moe_layer', executed_layer_ids=[selected],
                 compute_validation='reference_stage_costs_only',
                 unexecuted_layer_ids=[i for i in range(len(model['layers'])) if i != selected])
    return audit
