"""Compose checked layer DAGs into one wave, with only rank-local boundaries."""
from copy import deepcopy

from .plan import MAX_TRANSFERS, build_plan, integer, require, validate_plan

MAX_SEGMENT_LAYERS = 128  # Explicit host-side graph budget; target scale is unvalidated.


def common_input(layer):
    variable = {'layer_id', 'tokens', 'compute_profile_ns', 'schema_version'}
    if layer['schema_version'] == 'moe-ub-execution-plan/3':
        variable |= {'expert_placement', 'top_k'}
    return {k:v for k,v in layer['input'].items() if k not in variable}


def compose_layers(layers):
    require(isinstance(layers, list) and layers, 'Require a nonempty layer segment')
    first = layers[0]
    batched = first['schema_version'] == 'moe-ub-execution-plan/3'
    require((1 <= len(layers) <= MAX_SEGMENT_LAYERS) if batched else (2 <= len(layers) <= 32),
            'Layer count exceeds composition budget')
    ids = [p['input']['layer_id'] for p in layers]
    require(all(type(i) is int and 0 <= i < (2**31 if batched else 32) for i in ids) and
            ids == list(range(ids[0], ids[0]+len(ids))), 'Invalid consecutive layer order')
    common = common_input(first)
    def tokens(p):
        return [{k:v for k,v in t.items() if k not in ('layer_id','expert_ids')} for t in p['input']['tokens']]
    ranks = first['rank_ids']
    merged = deepcopy(first)
    merged.update(schema_version='moe-ub-multilayer-plan/2' if batched else 'moe-ub-multilayer-plan/1',
        layers=deepcopy(layers), executed_layer_ids=ids,
        input=dict(common, schema_version='moe-ub-multilayer-input/2' if batched else 'moe-ub-multilayer-input/1', executed_layer_ids=ids),
        operations=[], transfers=[], rank_graphs=[dict(rank_id=r,nodes=[],layer_phases=[]) for r in ranks],
        expert_input_counts=[0 for r in ranks])
    merged.pop('input_provenance', None)
    if batched:
        # Shapes and physical contributions are layer-specific, kept in layers.
        merged.pop('compute_inputs', None)
        merged.pop('contributions', None)
    previous = {r:[] for r in ranks}
    identities = {k:set() for k in ('transfer_id','ub_task_id','comm_tag')}
    operations = set()
    for layer in layers:
        validate_plan(layer)
        layer_id = layer['input']['layer_id']
        require(layer['schema_version']==first['schema_version'] and
                layer['schema_version'] in ('moe-ub-execution-plan/1','moe-ub-execution-plan/3') and
                layer['input']['role']=='D' and layer['input']['compute_profile_ns']['next_layer']==0,
                'Multilayer decode must omit synthetic next_layer work')
        require(common_input(layer)==common and tokens(layer)==tokens(first),
                'Layer shared configuration or Token continuity mismatch')
        for k in ('graph_id','wave_id','rank_ids','rank_to_ub_node','ready_ns','instance_batches',
                  'send_slots_per_rank','communication_template','sender_completion'):
            require(layer[k]==first[k], 'Layer identity/configuration mismatch: '+k)
        offset = {g['rank_id']:len(g['nodes']) for g in merged['rank_graphs']}
        for op in layer['operations']:
            require(op['layer_id']==layer_id and op['operation_id'] not in operations and
                op['wave_id']==first['wave_id'], 'Duplicate or mismatched layer operation')
            operations.add(op['operation_id']);merged['operations'].append(deepcopy(op))
        for t in layer['transfers']:
            for k in identities:
                if k in t:
                    require(t[k] not in identities[k], 'Duplicate multilayer '+k);identities[k].add(t[k])
            shifted = deepcopy(t)
            shifted['send_node_id'] += offset[t['src_rank']]
            shifted['recv_node_id'] += offset[t['dst_rank']]
            merged['transfers'].append(shifted)
        for index,(src,dst) in enumerate(zip(layer['rank_graphs'],merged['rank_graphs'])):
            rank=src['rank_id'];base=offset[rank]
            require([n['node_id'] for n in src['nodes']]==list(range(1,len(src['nodes'])+1)),
                    'Layer node ids must be contiguous')
            entry=previous[rank]
            used={d for n in src['nodes'] for d in n['deps']}
            terminal=[n['node_id']+base for n in src['nodes'] if n['node_id'] not in used] or entry
            for original in src['nodes']:
                node=deepcopy(original)
                node.update(node_id=original['node_id']+base,layer_id=layer_id,
                    deps=[d+base for d in original['deps']] if original['deps'] else list(entry))
                dst['nodes'].append(node)
            phase = dict(layer_id=layer_id,entry_deps=list(entry),terminal_nodes=terminal,
                phase_nodes={k:None if v is None else v+base for k,v in src['phase_nodes'].items()})
            if 'local_prepare_nodes' in src:
                phase['local_prepare_nodes'] = {k:None if v is None else v+base for k,v in src['local_prepare_nodes'].items()}
            dst['layer_phases'].append(phase)
            previous[rank]=terminal
            merged['expert_input_counts'][index]+=layer['expert_input_counts'][index]
    require(len(merged['transfers'])<=MAX_TRANSFERS, 'Multilayer plan exceeds 4096 transfers')
    return merged


def build_multilayer(configs, identities, provenance):
    require(configs and len(configs)==len(provenance) and
            all(c.get('schema_version') == configs[0].get('schema_version') and
                c.get('schema_version') in ('moe-ub-layer-fixture/1','moe-ub-layer-fixture/3') for c in configs),
            'Multilayer composition requires uniform layer schema 1 or 3')
    layers=[];ordinal=1
    for config,records in zip(configs,provenance):
        child=build_plan(config)
        mapping={t['transfer_id']:ordinal+i for i,t in enumerate(child['transfers'])}
        for t in child['transfers']:
            identity=mapping[t['transfer_id']]
            t.update(transfer_id=identity,comm_tag=identity)
            if 'ub_task_id' in t:
                t['ub_task_id']=identity
        for graph in child['rank_graphs']:
            for node in graph['nodes']:
                if 'transfer_id' in node:node['transfer_id']=mapping[node['transfer_id']]
        ordinal+=len(child['transfers'])
        child['input_provenance']=dict(route_records=deepcopy(records))
        layers.append(child)
    plan=compose_layers(layers)
    identities.assign(plan)
    validate_multilayer(plan)
    return plan


def validate_multilayer(plan):
    expected=compose_layers(plan['layers'])
    # Evidence and declared data sources are added by the service after graph
    # construction. Every executable field must match recomposition exactly.
    for key,value in expected.items():
        if key not in ('compute_source','route_source'):
            require(plan.get(key)==value, 'Multilayer composition mismatch: '+key)
    for graph in plan['rank_graphs']:
        total=sum(n.get('duration_ns',0) for n in graph['nodes'])
        integer(plan['ready_ns']+total,'multilayer computation time bound')
