"""Complete MLA Decode DAG using reference compute and existing CANN children.

All stages use the existing single endpoint compute slot. Shared FFN is ready
beside the routed branch; its overlap with real network waits follows the DAG.
"""
from copy import deepcopy

from .mla_capacity import audit_inventory, validate_model
from .model_capacity import fields, communication_minimum
from .multilayer import MAX_SEGMENT_LAYERS, build_children, common_input
from .plan import MAX_TIME_NS, integer, require, validate_plan

COMMON = ('attention_norm', 'mla_prologue', 'mla_output', 'attention_residual', 'ffn_norm', 'ffn_residual')
MOE = ('router', 'dispatch_quant_pack', 'combine_unpack_merge', 'shared_expert', 'shared_routed_add')
HEAD = ('embedding', 'final_norm', 'lm_head', 'sampling')


def model_structure(model):
    result=deepcopy(model)
    for layer in result['layers']:layer.pop('expert_placement',None)
    return result


def validate_profile(model, profile):
    fields(profile,'schema_version basis model_structure per_token_ns layers','full model reference profile')
    require(profile['schema_version']=='moe-ub-full-model-cost/1' and profile['basis']=='declared_reference',
            'Full model requires declared reference costs')
    require(profile['model_structure']==model_structure(model),'Full model cost domain differs from model structure')
    fields(profile['per_token_ns'],' '.join(HEAD),'full model head costs')
    for name,value in profile['per_token_ns'].items():integer(value,name,1)
    require(isinstance(profile['layers'],list) and len(profile['layers'])==len(model['layers']),
            'Full model costs must cover every block')
    for layer,p in zip(model['layers'],profile['layers']):
        moe=layer['kind']=='moe'
        fields(p,'layer_id per_token_ns attention_per_context_token_ns'+(' routed_expert_per_input_ns' if moe else ''),
               'full model block costs')
        integer(p['layer_id'],'cost layer id',layer['layer_id'],layer['layer_id'])
        fields(p['per_token_ns'],' '.join(COMMON+(MOE if moe else ('dense_ffn',))),'per token block costs')
        for name,value in p['per_token_ns'].items():
            absent=name in ('shared_expert','shared_routed_add') and layer['shared_experts']==0
            integer(value,name,0 if absent else 1,0 if absent else MAX_TIME_NS)
        integer(p['attention_per_context_token_ns'],'attention context cost',1)
        if moe:integer(p['routed_expert_per_input_ns'],'routed expert cost',1)


def audit_full_capacity(model, memory, layer):
    require(model.get('schema_version')=='moe-ub-model/2','Full model execution requires MLA model/2')
    result=audit_inventory(model,memory,layer['rank_ids'],communication_minimum_bytes=communication_minimum(layer))
    selected=integer(layer['layer_id'],'full model selected layer',0,len(model['layers'])-1)
    row=model['layers'][selected]
    require(row['kind']=='moe' and row['top_k']==layer['top_k'] and row['expert_placement']==layer['expert_placement'],
            'Full model layer structure/placement mismatch')
    require(model['hidden_size']==layer['hidden_dim'] and model['dtypes']==layer['compute_dtypes'],
            'Full model execution dimensions/dtypes differ')
    require(layer['template'] in ('cann_a3_v850_fullmesh_v1','cann_a3_v850_native_v1') and layer['role']=='D' and
            'operator_profile' not in layer,'Full model currently requires CANN Decode reference compute')
    return result


def compose_full_model(layers, model, profile, *, _retain_layers=False):
    # Public composition copies the caller's children. Internal build owns its
    # children; validation reads existing children without changing them. Both
    # may retain that list instead of duplicating it. All checks still run.
    require(isinstance(layers,list) and layers,'Full MoE model requires communication layers')
    first=layers[0];ranks=first['rank_ids']
    validate_model(model,ranks);validate_profile(model,profile)
    require(len(model['layers'])<=MAX_SEGMENT_LAYERS,'Full model exceeds host layer budget')
    ids=[r['layer_id'] for r in model['layers']]
    moe_ids=[r['layer_id'] for r in model['layers'] if r['kind']=='moe']
    require([l['input']['layer_id'] for l in layers]==moe_ids,'Full model requires every MoE layer exactly once')
    common=common_input(first)
    token_keys=lambda p:[{k:v for k,v in t.items() if k not in ('layer_id','expert_ids')} for t in p['input']['tokens']]
    seen={k:set() for k in ('transfer_id','ub_task_id','comm_tag','operation_id')}
    for layer in layers:
        validate_plan(layer);c=layer['input'];lid=c['layer_id'];spec=model['layers'][lid];cost=profile['layers'][lid]
        require(layer['schema_version']=='moe-ub-execution-plan/3' and c['template'] in ('cann_a3_v850_fullmesh_v1','cann_a3_v850_native_v1')
                and c['role']=='D' and 'operator_profile' not in c,
                'Full model requires CANN layer/3')
        require(common_input(layer)==common and token_keys(layer)==token_keys(first),'Full model shared configuration/Token continuity mismatch')
        require(c['hidden_dim']==model['hidden_size'] and c['compute_dtypes']==model['dtypes'] and
                c['expert_placement']==spec['expert_placement'] and c['top_k']==spec['top_k'],
                'Full model communication layer differs from model')
        require(c['compute_profile_ns']==dict(attention_route_pack=cost['per_token_ns']['dispatch_quant_pack'],
            expert_per_input=cost['routed_expert_per_input_ns'],expert_local_merge=0,
            combine_unpack_merge=cost['per_token_ns']['combine_unpack_merge'],next_layer=0),
            'Full model child costs must exclude attention/router/shared work')
        for op in layer['operations']:
            key=op['operation_id'];require(key not in seen['operation_id'],'Duplicate full model operation');seen['operation_id'].add(key)
        for t in layer['transfers']:
            for key in ('transfer_id','ub_task_id','comm_tag'):
                if key in t:
                    require(t[key] not in seen[key],'Duplicate full model '+key);seen[key].add(t[key])
    del seen
    tokens=token_keys(first)
    require(all(t['token_index']<model['max_position_embeddings'] for t in tokens),'Full model context exceeds model')
    removed={'input_provenance','compute_inputs','contributions','compute_evaluation','communication_buffers'}
    replaced={'schema_version','input','operations','transfers','rank_graphs','expert_input_counts'}
    # Preserve original key order without copying values immediately discarded
    # by update (especially the first layer's graph and transfer records).
    merged={k:None if k in replaced else deepcopy(v) for k,v in first.items() if k not in removed}
    # Keep merged input detached even when layers are retained: edits must not
    # silently change the child inputs used by independent reconstruction.
    merged.update(schema_version='moe-ub-full-model-plan/1',layers=layers if _retain_layers else deepcopy(layers),model_spec=deepcopy(model),
        full_model_profile=deepcopy(profile),executed_layer_ids=ids,communication_layer_ids=moe_ids,
        input=dict(deepcopy(common),schema_version='moe-ub-full-model-input/1',executed_layer_ids=ids),
        operations=[deepcopy(op) for l in layers for op in l['operations']],transfers=[],rank_graphs=[],
        expert_input_counts=[sum(l['expert_input_counts'][i] for l in layers) for i in range(len(ranks))])
    offsets={}
    for ri,rank in enumerate(ranks):
        local=[t for t in tokens if t['src_rank']==rank];n=len(local);context=sum(t['token_index']+1 for t in local)
        graph=dict(rank_id=rank,nodes=[],layer_phases=[],model_stages=[]);nodes=graph['nodes'];previous=[]
        def comp(stage,work,deps,lid=None):
            integer(work,'full model stage duration',0,MAX_TIME_NS-first['ready_ns'])
            deps=sorted(set(deps));node_id=None
            if work:
                node_id=len(nodes)+1
                nodes.append(dict(node_id=node_id,rank_id=rank,kind='COMP',name=stage,model_stage=stage,
                    layer_id=lid,duration_ns=work,resource_id='gpu_compute',deps=deps))
            graph['model_stages'].append(dict(stage=stage,layer_id=lid,node_id=node_id,duration_ns=work,
                entry_deps=deps,batch_size=n,context_tokens=[t['token_index']+1 for t in local]))
            return [node_id] if node_id is not None else deps
        previous=comp('embedding',n*profile['per_token_ns']['embedding'],previous)
        children={l['input']['layer_id']:l for l in layers}
        for spec,cost in zip(model['layers'],profile['layers']):
            lid=spec['layer_id'];per=cost['per_token_ns']
            for stage in ('attention_norm','mla_prologue'):
                previous=comp(stage,n*per[stage],previous,lid)
            previous=comp('mla_attention',context*cost['attention_per_context_token_ns'],previous,lid)
            for stage in ('mla_output','attention_residual','ffn_norm'):
                previous=comp(stage,n*per[stage],previous,lid)
            if spec['kind']=='dense':
                previous=comp('dense_ffn',n*per['dense_ffn'],previous,lid)
            else:
                branch=list(previous)
                routed=comp('router',n*per['router'],branch,lid)
                child=children[lid]['rank_graphs'][ri];base=len(nodes);offsets[lid,rank]=base
                # One immutable integer per node, reused by its incoming edges.
                # Dependency lists remain independent mutable lists as before.
                shifted={node['node_id']:node['node_id']+base for node in child['nodes']}
                used={d for node in child['nodes'] for d in node['deps']}
                terminal=[shifted[node['node_id']] for node in child['nodes'] if node['node_id'] not in used] or routed
                for original in child['nodes']:
                    node={k:None if k=='deps' else deepcopy(v) for k,v in original.items()}
                    node.update(node_id=shifted[original['node_id']],layer_id=lid,
                        deps=[shifted[d] for d in original['deps']] if original['deps'] else list(routed))
                    if node['kind']=='COMP' and original['node_id']==child['phase_nodes']['prepare']:
                        node.update(name='dispatch-quant-pack',model_stage='dispatch_quant_pack')
                    nodes.append(node)
                phase=dict(layer_id=lid,entry_deps=routed,terminal_nodes=terminal,
                    phase_nodes={k:None if v is None else shifted[v] for k,v in child['phase_nodes'].items()})
                if 'local_prepare_nodes' in child:
                    phase['local_prepare_nodes']={k:None if v is None else shifted[v] for k,v in child['local_prepare_nodes'].items()}
                graph['layer_phases'].append(phase)
                shared=comp('shared_expert',n*spec['shared_experts']*per['shared_expert'],branch,lid)
                previous=comp('shared_routed_add',n*per['shared_routed_add'],terminal+shared,lid)
            previous=comp('ffn_residual',n*per['ffn_residual'],previous,lid)
        for stage in ('final_norm','lm_head','sampling'):
            previous=comp(stage,n*profile['per_token_ns'][stage],previous)
        integer(first['ready_ns']+sum(node.get('duration_ns',0) for node in nodes),'full model time bound')
        graph['terminal_nodes']=previous;merged['rank_graphs'].append(graph)
    for child in layers:
        lid=child['input']['layer_id']
        for original in child['transfers']:
            t=deepcopy(original);t['send_node_id']+=offsets[lid,t['src_rank']];t['recv_node_id']+=offsets[lid,t['dst_rank']]
            merged['transfers'].append(t)
    from .resources import actual_usage, check_graph
    usage=check_graph(actual_usage(merged),common.get('resource_budget'))
    if 'resource_budget' in common:merged['resource_usage']=usage
    return merged


def build_full_model(configs, identities, provenance, model, profile):
    from .resources import full_usage, check_graph, actual_usage
    from .plan import validate_config
    for c in configs:validate_config(c)
    usage=check_graph(full_usage(configs,model),configs[0].get('resource_budget'))
    plan=compose_full_model(build_children(configs,provenance),model,profile,_retain_layers=True)
    require(actual_usage(plan)==usage,'Full model preflight count mismatch')
    identities.assign(plan);validate_full_model(plan)
    return plan


def validate_full_model(plan):
    expected=compose_full_model(plan['layers'],plan['model_spec'],plan['full_model_profile'],_retain_layers=True)
    for key,value in expected.items():
        if key not in ('compute_source','route_source'):
            require(plan.get(key)==value,'Full model composition mismatch: '+key)
