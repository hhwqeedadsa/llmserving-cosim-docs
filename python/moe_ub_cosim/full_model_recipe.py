"""Serializable full-model build inputs; never accept a prevalidated mutable plan."""
from copy import deepcopy

from .model_capacity import fields
from .plan import RunIdentities, integer, require


def identity_state(identities):
    return dict(transfer=identities.transfer,task=identities.task,tag=identities.tag,
                graphs=sorted(identities.graphs),operations=sorted(identities.operations))


def identities_from(state):
    fields(state,'transfer task tag graphs operations','graph identity state')
    for name in ('transfer','task','tag'):integer(state[name],name,1,2**64)
    for name in ('graphs','operations'):
        rows=state[name]
        require(isinstance(rows,list) and all(isinstance(v,str) for v in rows) and rows==sorted(set(rows)),
                'Invalid identity history')
    result=RunIdentities(state['transfer'],state['task'],state['tag'])
    result.graphs=set(state['graphs']);result.operations=set(state['operations'])
    return result


def prepare(service,candidate):
    configs,provenance,consumed=service.iteration_inputs(candidate)
    name=candidate['group_id']
    metadata=service.iteration_provenance(candidate,configs,provenance)
    metadata.update(execution_scope='full_model_decode_reference',
                    compute_source='full_model_stage_reference_with_context_cost')
    recipe=dict(schema_version='moe-ub-full-model-recipe/1',configs=configs,provenance=provenance,
        model=deepcopy(service.full_models[name]),profile=deepcopy(service.full_profiles[name]),
        identities=identity_state(service.identities),input_provenance=metadata,route_evidence=None)
    if hasattr(service,'route_evidence'):
        records=[]
        for config in configs:
            selected=[]
            for token in config['tokens']:
                key=token['request_id'],token['token_index'],token['layer_id']
                record=deepcopy(service.route_records[key])
                require(record['expert_ids']==token['expert_ids'],'Imported route changed during execution')
                selected.append(record)
            records.append(selected)
        recipe['route_evidence']=dict(records=records,source=service.route_evidence['source']['kind'],
            manifest=deepcopy(service.observation_config['route_manifest']))
    next_usage=None
    if service.resource_budget is not None:
        from .resources import full_usage,check_run
        next_usage=check_run(service.resource_usage,full_usage(configs,recipe['model']),service.resource_budget)
    return recipe,consumed,next_usage


def build_export(recipe,output):
    """Own construction, complete validation, fixed source annotations and writing."""
    from .full_model import build_full_model
    from .generate import _write_plan
    fields(recipe,'schema_version configs provenance model profile identities input_provenance route_evidence','graph recipe')
    require(recipe['schema_version']=='moe-ub-full-model-recipe/1','Unsupported graph recipe')
    ids=identities_from(recipe['identities'])
    plan=build_full_model(recipe['configs'],ids,recipe['provenance'],recipe['model'],recipe['profile'])
    metadata=recipe['input_provenance']
    fields(metadata,'group_id model_spec_sha256 route_records execution_scope compute_source calibration_status',
           'recipe provenance')
    require(metadata['group_id']==plan['input']['group_id'] and
        metadata['route_records']==[r for records in recipe['provenance'] for r in records] and
        metadata['execution_scope']=='full_model_decode_reference' and
        metadata['compute_source']=='full_model_stage_reference_with_context_cost' and
        metadata['calibration_status']=='unvalidated_reference','Recipe source declaration mismatch')
    plan['input_provenance']=deepcopy(metadata)
    plan['compute_source']='full_model_stage_reference_with_context_cost'
    evidence=recipe['route_evidence']
    if evidence is not None:
        fields(evidence,'records source manifest','recipe route evidence')
        require(isinstance(evidence['source'],str) and len(evidence['records'])==len(plan['layers']),
                'Recipe route source mismatch')
        records=[]
        for child,selected in zip(plan['layers'],evidence['records']):
            tokens=child['input']['tokens']
            require(len(tokens)==len(selected) and all(
                all(record[k]==token[k] for k in ('request_id','token_index','layer_id','expert_ids'))
                for token,record in zip(tokens,selected)),'Recipe route token mismatch')
            child['input_provenance']['route_records']=deepcopy(selected)
            child['route_source']=evidence['source'];records.extend(selected)
        plan['input_provenance'].update(route_records=deepcopy(records),route_manifest=deepcopy(evidence['manifest']))
        plan['route_source']=evidence['source']
    # Only declarations above may be added after the original full validation.
    # No execution field or caller-owned plan crosses this boundary.
    _write_plan(plan,output,compact=True)
    return dict(identities=identity_state(ids),graph_id=plan['graph_id'],
        transfers=len(plan['transfers']),nodes=sum(len(g['nodes']) for g in plan['rank_graphs']))
