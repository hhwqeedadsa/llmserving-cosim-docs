"""Finite consecutive MoE blocks from declared activations and ready KV."""
from collections import Counter
from copy import deepcopy
from pathlib import Path

from .plan import integer, require
from .model_capacity import fields

INITIAL_STATE=dict(simulation_ns=0,network='fresh',activations='first_block_input_ready',
    kv='ready_at_selected_decode_step',capacity='full_deployment')


def validate_segment(segment,model,tokens=None,ranks=None):
    fields(segment,'schema_version first_layer layer_count decode_step initial_state requests','finite segment')
    require(segment['schema_version']=='moe-ub-model-segment/1' and segment['initial_state']==INITIAL_STATE,
            'Segment requires explicit block-input activations, ready KV and a fresh network at time zero')
    integer(segment['initial_state']['simulation_ns'],'segment initial time',0,0)
    first=integer(segment['first_layer'],'first layer',0,len(model['layers'])-1)
    count=integer(segment['layer_count'],'layer count',1,len(model['layers'])-first)
    step=integer(segment['decode_step'],'source Decode step',0)
    ids=list(range(first,first+count))
    require(all(model['layers'][i]['kind']=='moe' for i in ids),'Segment must select consecutive original MoE blocks')
    require(isinstance(segment['requests'],list) and segment['requests'],'Segment has no declared initial requests')
    selected={}
    for row in segment['requests']:
        fields(row,'request_id rank_id prompt_tokens generated_tokens_at_trace_start token_index','segment initial request')
        require(isinstance(row['request_id'],str) and row['request_id'],'Invalid segment request identity')
        integer(row['rank_id'],'segment rank',0,2**31-2)
        p=integer(row['prompt_tokens'],'initial prompt tokens',1)
        g=integer(row['generated_tokens_at_trace_start'],'initial generated tokens',1)
        integer(row['token_index'],'initial token index',p+g-1+step,p+g-1+step)
        require(row['token_index']<model['max_position_embeddings'],'Segment context exceeds model')
        require(row['request_id'] not in selected,'Repeated segment request')
        selected[row['request_id']]=row
    if tokens is not None:
        expected={rid:row for rid,row in selected.items() if row['rank_id'] in ranks}
        actual={t['request_id']:t for t in tokens}
        require(set(actual)==set(expected) and len(actual)==len(tokens),'Segment must execute the complete declared request batch once')
        require(all(t['src_rank']==expected[rid]['rank_id'] and t['token_index']==expected[rid]['token_index']
                    for rid,t in actual.items()),'Segment request context differs from declared initial state')
    return ids


def select(service,layers,first,count,step):
    """Copy inline full-model inputs; preserve model, deployment and capacity."""
    require(service.get('schema_version')=='moe-ub-configured-service/4','Segment preparation requires full-model service/4 inputs')
    integer(step,'Decode step',0)
    result=deepcopy(service);segments=deepcopy(layers)
    scope=dict(schema_version='moe-ub-model-segment/1',first_layer=first,layer_count=count,
        decode_step=step,initial_state=deepcopy(INITIAL_STATE),requests=[])
    for request in result['requests']:
        require(request['arrival_ns']==0 and step<len(request['decode_routes']),
                'Segment requires all requests initially ready and the selected step present')
        p,g=request['prompt_tokens'],request['generated_tokens']
        scope['requests'].append(dict(request_id=str(request['request_id']),rank_id=request['rank_id'],
            prompt_tokens=p,generated_tokens_at_trace_start=g,token_index=p+g-1+step))
        require(all(str(i) in request['decode_routes'][step] for i in range(first,first+count)),
                'Source route omits a selected original layer')
        request['decode_routes']=[{str(i):deepcopy(request['decode_routes'][step][str(i)]) for i in range(first,first+count)}]
        request['generated_tokens']+=step
    by_name={g['group_id']:g for g in segments['groups']}
    for group in result['groups']:
        ids=validate_segment(scope,group['model_spec'])
        source=by_name[group['group_id']];original={l['layer_id']:l for l in source['layers']}
        require(set(ids)<=set(original),'Source layer configuration is incomplete')
        source['layers']=[original[i] for i in ids]
        ranks=source['layers'][0]['rank_ids'];counts=Counter(r['rank_id'] for r in result['requests'] if r['rank_id'] in ranks)
        require(counts and max(counts.values())<=min(group['scheduling'].values()),
                'All segment requests must fit the initial sequence/Token batch')
    result['schema_version']='moe-ub-configured-service/5';result['execution']['segment']=scope
    return result,segments


def select_source(source,layers,first,count,step):
    """Stream verified trace files when the source uses an evidence wrapper."""
    if source.get('schema_version')!='moe-ub-evidence-service/1':
        service,selected=select(source,layers,first,count,step)
        return service,selected,None
    from .route_evidence import select_routes
    fields(source,'schema_version run_id service route_manifest requests compute_profiles','segment route source')
    require(source['service']['schema_version']=='moe-ub-configured-service/4' and
            source['service']['run_id']==source['run_id'] and source['service']['requests']==[],
            'Route source requires an empty full-model service template')
    require(source['compute_profiles']==[],
            'Segment uses the declared full-model cost profile; separate operator profiles are unsupported')
    integer(step,'Decode step',0);integer(first,'first layer',0);integer(count,'layer count',1)
    requests=deepcopy(source['requests'])
    for row in requests:
        integer(row['decode_steps'],'source Decode steps',1)
        require(step<row['decode_steps'],'Selected step is outside the declared source window')
        row['first_decode_entry']+=step;row['decode_steps']=1
    models=[g['model_spec'] for g in source['service']['groups']]
    budget=source['service']['execution'].get('resource_budget')
    for group in layers['groups']:
        for layer in group['layers']:
            if first<=layer['layer_id']<first+count:
                require('resource_budget' not in layer or layer['resource_budget']==budget,
                        'Source layer/resource budget mismatch')
    normalized,records,evidence=select_routes(source['route_manifest'],requests,models,list(range(first,first+count)),compact=True)
    service=deepcopy(source['service']);service['requests']=normalized
    # The importer has already advanced context to the selected source entry.
    # Select that one supplied iteration, then retain the original window index.
    service,selected=select(service,layers,first,count,0)
    scope=service['execution']['segment'];scope['decode_step']=step
    for row in scope['requests']:row['generated_tokens_at_trace_start']-=step
    for model in models:validate_segment(scope,model)
    evidence.update(original_request_windows=deepcopy(source['requests']),selected_decode_step=step,
        records=[records[key] for key in records],eos_inferred=False)
    return service,selected,evidence


def _prepare(config_path,output):
    """Prepare portable run inputs, without graph generation or simulation."""
    import shutil
    from .user_io import read,relative,save,inventory
    from .plan_io import file_sha256
    config_path=Path(config_path).resolve();config=read(config_path);base=config_path.parent
    fields(config,'schema_version source_run first_layer layer_count decode_step','segment prepare')
    require(config['schema_version']=='moe-ub-segment-prepare/1','Unknown segment preparation schema')
    source=relative(base,config['source_run']);run=read(source)
    require(run['schema_version'] in ('moe-ub-run/1','moe-ub-segment-source/1'),
            'Segment source must declare public run inputs or a verified trace source')
    source_base=source.parent
    paths={k:relative(source_base,v,kind='directory' if k=='network' else 'file') for k,v in run['inputs'].items()}
    service,layers,route_evidence=select_source(read(paths['service']),read(paths['layer']),config['first_layer'],config['layer_count'],config['decode_step'])
    # Validate capacity and every selected route without constructing graphs.
    from .full_model_service import FullModelService
    class Preparation(FullModelService):
        def check_segment(self,layers,group):
            from .plan import validate_config
            for layer in layers:validate_config(layer)
    frontend=Preparation(service,layers)
    output=Path(output).resolve()
    save(output/'service.json',service);save(output/'layers.json',layers)
    shutil.copyfile(paths['system'],output/'system.json');shutil.copytree(paths['network'],output/'network')
    effective=deepcopy(run);effective['schema_version']='moe-ub-run/1'
    effective['inputs']=dict(service='service.json',layer='layers.json',system='system.json',network='network')
    for key in ('observation','record_policy','export_policy'):
        src=relative(source_base,run[key]);name=key.replace('_','-')+'.json'
        shutil.copyfile(src,output/name);effective[key]=name
    effective['backend']=str((source_base/run['backend']).resolve())
    effective['input_evidence']='preparation.json'
    for key in ('host','export_host'):
        if key in effective:effective[key]['artifact_roots']=[str((source_base/p).resolve()) for p in effective[key]['artifact_roots']]
    save(output/'run.json',effective)
    save(output/'preparation.json',dict(schema_version='moe-ub-segment-preparation/1',passed=True,
        source=dict(run_sha256=file_sha256(source),service_sha256=file_sha256(paths['service']),layer_sha256=file_sha256(paths['layer'])),
        segment=service['execution']['segment'],capacity=frontend.evidence,route_evidence=route_evidence,
        artifacts={k:v for k,v in inventory(output).items() if not k.startswith('logs/')},
        graph_generation_executed=False,simulation_executed=False))
    return dict(passed=True,output=str(output),run_config=str(output/'run.json'),
        layer_ids=list(range(config['first_layer'],config['first_layer']+config['layer_count'])),
        requests=len(service['requests']),scope='declared_initial_state_finite_moe_segment')


def prepare(config_path,output):
    from .user_io import budgeted_worker,host_policy,read,relative
    config_path=Path(config_path).resolve();config=read(config_path)
    source=relative(config_path.parent,config['source_run']);run=read(source)
    result=budgeted_worker('moe_ub_cosim.segment',[str(config_path)],output,
        host_policy(run['host'],source.parent),'preparation.json')
    result['run_config']=str(Path(output).resolve()/'run.json')
    return result


if __name__=='__main__':
    import sys
    _prepare(sys.argv[1],sys.argv[2])
