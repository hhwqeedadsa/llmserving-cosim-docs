"""Audited Mixtral inputs for one selected layer; no model execution or routing inference."""
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path

from .plan import integer, require, validate_config
from .service import LLM, ROOT, validate_service

MODEL = 'mistralai/Mixtral-8x7B-v0.1'
MODEL_PATH = LLM / 'configs/model' / (MODEL + '.json')
PROFILE_PATH = LLM / 'profiler/v0/perf_models/H100' / MODEL / 'tp1/layers.csv'
DENSE_OPS = ('input_layernorm', 'q_proj', 'k_proj', 'v_proj', 'o_proj', 'post_layernorm')


def file_evidence(path):
    path = Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def read_verified(ref):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'Invalid file reference')
    require(isinstance(ref['path'], str) and ref['path'], 'Invalid input path')
    path = (ROOT / ref['path']).resolve()
    require(path.is_file(), 'Missing input file: ' + str(path))
    content = path.read_bytes()
    require(hashlib.sha256(content).hexdigest() == ref['sha256'], 'Input SHA256 mismatch: ' + str(path))
    return path, content


def audit_model(ref, layer, memory, dtype):
    path, content = read_verified(ref)
    require(path == MODEL_PATH.resolve(), 'Only the locked Mixtral config is implemented')
    c = json.loads(content)
    expected = dict(model_type='mixtral', hidden_size=4096, intermediate_size=14336,
                    num_attention_heads=32, num_key_value_heads=8, num_hidden_layers=32,
                    num_local_experts=8, num_experts_per_tok=2, vocab_size=32000,
                    max_position_embeddings=32768, tie_word_embeddings=False)
    require(all(c.get(k) == v and type(c[k]) is type(v) for k, v in expected.items()),
            'Unsupported Mixtral architecture/config')
    require(c['architectures'] == ['MixtralForCausalLM'] and c['hidden_act'] == 'silu' and
            c.get('head_dim', 128) == 128 and c['sliding_window'] is None,
            'Unsupported Mixtral layer structure')
    require(dtype in ('float16', 'bfloat16'), 'Unsupported compute/weight/KV dtype')
    require(layer['role'] == 'D' and layer['hidden_dim'] == c['hidden_size'] and
            layer['top_k'] == c['num_experts_per_tok'] and 0 <= layer['layer_id'] < c['num_hidden_layers'] and
            layer['rank_ids'] == [0, 1, 2, 3] and layer['expert_placement'] == [0, 0, 1, 1, 2, 2, 3, 3] and
            layer['dispatch_element_bytes'] == layer['combine_element_bytes'] == 2,
            'Selected layer/model/EP4 deployment mismatch')
    require(set(memory) == {'device_bytes', 'workspace_bytes', 'communication_buffer_bytes', 'block_size'},
            'Invalid memory budget fields')
    for field in ('device_bytes', 'workspace_bytes', 'communication_buffer_bytes'):
        integer(memory[field], field, 1, 128 * 2**30)
    require(type(memory['block_size']) is int and memory['block_size'] == 16, 'Require 16-token KV blocks')
    h, f, v, n, kv = c['hidden_size'], c['intermediate_size'], c['vocab_size'], c['num_hidden_layers'], 1024
    # Dense weights are replicated across EP ranks. Only experts are partitioned.
    components = dict(embedding=2*v*h, final_norm=2*h, lm_head=2*v*h,
        block_norms=n*2*2*h, block_qkv=n*2*h*(h+2*kv), block_o=n*2*h*h,
        block_router=n*2*h*8, block_experts=n*2*3*h*f*2)
    weights = sum(components.values())
    bpt = 2*kv*n*2  # K/V, GQA heads, all layers, two bytes; TP=PP=1.
    block = bpt * memory['block_size']
    # Conservatively retain all vectors in the bounded four-token graph at once.
    minimum_buffer = max(4*2*2*h*2, 2*layer['send_slots_per_rank']*layer['chunk_bytes'])
    require(memory['communication_buffer_bytes'] >= minimum_buffer, 'Communication buffer below graph bound')
    budget = memory['device_bytes'] - weights - memory['workspace_bytes'] - memory['communication_buffer_bytes']
    require(budget >= block, 'Full model/reservations leave less than one KV block')
    return dict(schema_version='moe-ub-model-audit/1', model=MODEL, model_file=deepcopy(ref),
        model_config=c, config_dtype=c['torch_dtype'], execution_dtype=dtype, tp=1, pp=1, ep=4,
        architecture_basis=file_evidence(LLM / 'profiler/models/mixtral.yaml'),
        layer_structure=[dict(layer_id=i, kind='moe', experts=8, top_k=2) for i in range(n)],
        execution_scope='single_selected_moe_layer', executed_layer_ids=[layer['layer_id']],
        unexecuted_layer_ids=[i for i in range(n) if i != layer['layer_id']],
        full_model_weights_per_rank=components, weight_bytes_per_rank=weights,
        kv_bytes_per_token_per_rank=bpt, kv_block_bytes=block, kv_capacity_blocks=budget//block,
        kv_capacity_bytes=budget//block*block, unallocated_bytes=budget%block,
        communication_buffer_minimum_bytes=minimum_buffer, memory=deepcopy(memory),
        memory_basis='analytical_weights_and_KV_with_user_reserved_workspace_and_buffers',
        full_model_execution_validated=False, target_deployment_validated=False)


def import_routes(spec, requests, layer, layer_ids=None):
    require(set(spec) == {'file', 'kind', 'model', 'index_convention', 'description'}, 'Invalid route source fields')
    require(spec['kind'] in ('synthetic', 'measured') and spec['model'] == MODEL and
            spec['index_convention'] == 'absolute_input_position' and
            isinstance(spec['description'], str) and spec['description'].strip(), 'Unsupported route source')
    _, content = read_verified(spec['file'])
    selected = [layer['layer_id']] if layer_ids is None else layer_ids
    expected = {(str(r['request_id']), r['generated_tokens']-1+i, layer_id)
                for r in requests for i in range(r['decode_steps']) for layer_id in selected}
    by_id = {str(r['request_id']): r for r in requests}
    rows, indices = {}, set()
    for line_no, line in enumerate(content.decode('utf-8').splitlines(), 1):
        require(bool(line.strip()), 'Blank route record')
        source = json.loads(line)
        require(set(source) == {'schema_version','request_id','phase','source_token_index',
            'layer_id','expert_ids','source_index'} and source['schema_version'] == 'moe-ub-route-source/1',
            'Invalid route record fields/schema')
        require(source['request_id'] in by_id and source['phase'] == 'decode', 'Unknown request or non-decode route')
        integer(source['source_token_index'], 'source_token_index')
        integer(source['source_index'], 'source_index')
        integer(source['layer_id'], 'route layer_id', 0, 31)
        require(source['source_index'] not in indices, 'Duplicate source_index')
        indices.add(source['source_index'])
        normalized = source['source_token_index'] - by_id[source['request_id']]['prompt_tokens']
        key = (source['request_id'], normalized, source['layer_id'])
        require(key in expected and key not in rows, 'Unexpected/duplicate route identity or index')
        experts = source['expert_ids']
        require(isinstance(experts, list) and len(experts) == 2 and
                all(type(e) is int and 0 <= e < 8 for e in experts) and len(set(experts)) == 2,
                'Invalid route experts/Top-K')
        rows[key] = dict(request_id=key[0], phase='decode', token_index=normalized, layer_id=key[2],
            expert_ids=experts, source_line=line_no, source_index=source['source_index'],
            source_token_index=source['source_token_index'], source_file=deepcopy(spec['file']))
    require(set(rows) == expected, 'Missing continuous route records')
    return rows


def import_compute(spec, dtype, layer):
    profile = deepcopy(layer['compute_profile_ns'])
    if spec == {'kind': 'fixed_reference'}:
        return profile, dict(kind='fixed_reference', phase_durations_ns=profile,
            calibration_status='unvalidated_reference', target_profile_available=False)
    require(set(spec) == {'kind', 'file', 'prepare_residual_ns'} and
            spec['kind'] == 'h100_fp16_dense_reference', 'Unsupported compute source')
    require(dtype == 'float16', 'H100 legacy profile requires explicit float16 execution')
    path, content = read_verified(spec['file'])
    require(path == PROFILE_PATH.resolve(), 'Only the audited H100 TP1 profile is implemented')
    integer(spec['prepare_residual_ns'], 'prepare_residual_ns', 1, 10**9)
    reader = csv.DictReader(io.StringIO(content.decode('utf-8')))
    require(reader.fieldnames == ['layer_name','input','kv_cache','tp_size','latency(ns)'], 'Unsupported profile CSV')
    rows, seen = {}, set()
    for line_no, row in enumerate(reader, 2):
        key = (row['layer_name'], int(row['input']), int(row['kv_cache']), int(row['tp_size']))
        require(key not in seen, 'Duplicate profile key')
        seen.add(key)
        if key[0] in DENSE_OPS and key[1:] == (1, 0, 1):
            duration = integer(int(row['latency(ns)']), 'profile duration', 1, 10**9)
            output = 1024 if key[0] in ('k_proj','v_proj') else 4096
            rows[key[0]] = dict(operator=key[0], source_line=line_no, duration_ns=duration,
                input_tokens=1, source_kv_cache=0, tp=1, input_shape=[1,4096],
                output_shape=[1,output], context_dependency='none_for_this_operator')
    require(set(rows) == set(DENSE_OPS), 'Missing exact one-token dense profile rows')
    profile['attention_route_pack'] = sum(row['duration_ns'] for row in rows.values()) + spec['prepare_residual_ns']
    return profile, dict(kind=spec['kind'], file=deepcopy(spec['file']), device='H100', dtype='float16',
        dtype_basis=file_evidence(LLM/'profiler/v0/profiler/layers/main.py'),
        implementation_basis=file_evidence(LLM/'profiler/v0/models/mixtral.py'),
        weights_basis='profiler_constructs_model_from_config_without_loading_checkpoint',
        layer_basis='CSV_has_no_layer_identity; shape_level_reference_for_selected_layer',
        rows=[rows[k] for k in DENSE_OPS], prepare_residual_ns=spec['prepare_residual_ns'],
        replacement='whole_prepare_phase_replaced_by_sum_of_six_dense_rows_plus_explicit_residual',
        unprofiled=['attention','rope','router_and_pack','expert','local_merge','combine_unpack_merge','next_layer'],
        phase_durations_ns=profile, scope='one_token_per_active_rank_selected_layer',
        context_policy='dense_rows_do_not_read_KV; attention_remains_fixed_reference',
        extrapolation_count=0, calibration_status='unvalidated_reference', target_profile_available=False,
        historical_metadata_limit='device_from_pinned_path; dtype_from_pinned_profiler; no_per_run_hardware_manifest')


def load_inputs(config, layer):
    multi = config.get('schema_version') == 'moe-ub-multilayer-service/1'
    fields = {'schema_version','run_id','model','model_file','dtype','memory','requests','routes','compute'}
    require(set(config) == fields | ({'layer_profiles'} if multi else set()) and
            config['schema_version'] in ('moe-ub-data-service/1','moe-ub-multilayer-service/1') and config['model'] == MODEL,
            'Unsupported data service config')
    requests = config['requests']
    require(isinstance(requests, list) and 1 <= len(requests) <= 64, 'Require 1..64 data requests')
    ids = set()
    for r in requests:
        require(set(r) == {'request_id','rank_id','arrival_ns','prompt_tokens','generated_tokens','decode_steps'},
                'Invalid data request')
        for field in ('request_id','rank_id','arrival_ns','prompt_tokens','generated_tokens','decode_steps'):
            integer(r[field], field, 1 if field in ('prompt_tokens','generated_tokens','decode_steps') else 0)
        require(r['request_id'] not in ids and r['rank_id'] < 4 and r['decode_steps'] <= 32 and
                r['prompt_tokens'] + r['generated_tokens'] + r['decode_steps'] <= 32768,
                'Invalid request identity/rank/context/range')
        ids.add(r['request_id'])
    audit = audit_model(config['model_file'], layer, config['memory'], config['dtype'])
    selected = [layer['layer_id']]
    if multi:
        rows = config['layer_profiles']
        require(isinstance(rows,list) and 2 <= len(rows) <= 32 and
                all(set(r)=={'layer_id','compute_profile_ns'} for r in rows), 'Invalid layer profiles')
        selected = [r['layer_id'] for r in rows]
        require(all(type(i) is int and 0<=i<32 for i in selected) and selected[0]==layer['layer_id'] and
                selected==list(range(selected[0],selected[0]+len(rows))), 'Invalid consecutive layer order')
        require(config['compute']=={'kind':'fixed_reference'}, 'Multilayer requires explicit per-layer reference compute')
        profile = {}
        for row in rows:
            candidate = deepcopy(layer)
            candidate.update(layer_id=row['layer_id'],compute_profile_ns=row['compute_profile_ns'])
            for token in candidate['tokens']:token['layer_id']=row['layer_id']
            validate_config(candidate)
            require(row['compute_profile_ns']['next_layer']==0, 'Multilayer must omit synthetic next_layer work')
            profile[row['layer_id']] = deepcopy(row['compute_profile_ns'])
        audit.update(execution_scope='consecutive_moe_layer_segment',executed_layer_ids=selected,
            unexecuted_layer_ids=[i for i in range(32) if i not in selected],
            communication_buffer_minimum_bytes=audit['communication_buffer_minimum_bytes']*len(rows))
        require(config['memory']['communication_buffer_bytes']>=audit['communication_buffer_minimum_bytes'],
                'Communication buffer below multilayer graph bound')
        compute = dict(kind='per_layer_fixed_reference',layer_profiles=deepcopy(rows),
            scope='consecutive_moe_layer_segment',next_layer_policy='omitted_actual_next_layer_dependency',
            calibration_status='unvalidated_reference',target_profile_available=False)
    else:
        profile, compute = import_compute(config['compute'], config['dtype'], layer)
    routes = import_routes(config['routes'], requests, layer, selected)
    legacy = dict(schema_version='moe-ub-service-fixture/1', run_id=config['run_id'], mode='fixture',
        execution=dict(entry_mode='service'), model=MODEL,
        npu_memory_gib=(config['memory']['device_bytes']+2**30-1)//2**30, requests=[])
    for source in requests:
        row = {k:v for k,v in source.items() if k != 'decode_steps'}
        row['decode_routes'] = [routes[str(source['request_id']), source['generated_tokens']-1+i, layer['layer_id']]['expert_ids']
                                for i in range(source['decode_steps'])]
        legacy['requests'].append(row)
        peak = source['prompt_tokens'] + source['generated_tokens'] + source['decode_steps'] - 1
        require((peak+15)//16 <= audit['kv_capacity_blocks'], 'Request lifetime exceeds mapped rank KV capacity')
    validate_service(legacy, layer)
    evidence = dict(schema_version='moe-ub-data-evidence/1', model=audit, route_source=deepcopy(config['routes']),
                    normalized_routes=list(routes.values()), compute=compute,
                    route_validation='import_contract_only' if config['routes']['kind']=='synthetic' else 'imported_trace_contract',
                    communication_template_basis='unvalidated_reference')
    return legacy, routes, profile, evidence
