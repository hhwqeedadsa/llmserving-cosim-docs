"""Hash-checked route files with explicit source indexing and no EOF-to-EOS rule."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .data_inputs import read_verified
from .model_capacity import fields
from .plan import integer, require


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        require(key not in result, 'Duplicate JSON object key: '+key)
        result[key] = value
    return result


def read_json(ref):
    path,content = read_verified(ref)
    return path,json.loads(content, object_pairs_hook=unique_object)


def validate_manifest(manifest):
    fields(manifest,'schema_version source model layers files','route manifest')
    require(manifest['schema_version']=='moe-ub-route-manifest/1','Unsupported route manifest')
    source = manifest['source']
    fields(source,'kind dataset_id dataset_revision collector_model_id collector_model_revision '
           'runtime_revision precision usage_terms source_url unknown_fields','route source')
    require(source['kind'] in ('synthetic_test','published_model_trace','captured_model_trace'), 'Invalid route source kind')
    require(isinstance(source['unknown_fields'],list) and len(set(source['unknown_fields']))==len(source['unknown_fields']),
            'Invalid unknown source fields')
    for key in ('dataset_id','dataset_revision','collector_model_id','precision','usage_terms','source_url'):
        require(isinstance(source[key],str) and source[key].strip(), 'Missing route source: '+key)
    for key in ('collector_model_revision','runtime_revision'):
        require((source[key] is None and key in source['unknown_fields']) or
                (isinstance(source[key],str) and bool(source[key]) and key not in source['unknown_fields']),
                'Unknown collection revision must be explicit: '+key)
    if source['kind']!='synthetic_test':
        require(re.fullmatch('[0-9a-f]{40,64}',source['dataset_revision']), 'Real traces require a fixed dataset revision')
    if source['kind']=='captured_model_trace':
        require(source['collector_model_revision'] and source['runtime_revision'], 'Captured routes require fixed weights/runtime')
    model=manifest['model'];fields(model,'name hidden_dim num_layers routing_precision','route model')
    require(isinstance(model['name'],str) and model['name'] and model['routing_precision']==source['precision'],
            'Route model/precision mismatch')
    integer(model['hidden_dim'],'route hidden_dim',1);integer(model['num_layers'],'route layers',1,4096)
    layers=manifest['layers'];require(isinstance(layers,list) and len(layers)==model['num_layers'],'Incomplete route layer inventory')
    for i,layer in enumerate(layers):
        fields(layer,'layer_id kind num_experts top_k','route layer')
        integer(layer['layer_id'],'source layer_id',0,len(layers)-1)
        require(layer['layer_id']==i and layer['kind'] in ('dense','moe'),'Invalid route layer identity/type')
        if layer['kind']=='dense':
            integer(layer['num_experts'],'dense experts',0,0)
            integer(layer['top_k'],'dense Top-K',0,0)
        else:
            integer(layer['num_experts'],'route experts',1,2**31-1)
            integer(layer['top_k'],'route Top-K',1,layer['num_experts'])
    files=manifest['files'];require(isinstance(files,list) and 1<=len(files)<=4096,'Invalid route file budget')
    return manifest


def inspect_raw(content,layers):
    """Selection may inspect a prefill-only file; it remains ineligible for Decode."""
    return validate_raw(json.loads(content,object_pairs_hook=unique_object),layers,require_decode=False)


def validate_raw(raw,layers,require_decode=True):
    require(isinstance(raw,list) and (2 if require_decode else 1)<=len(raw)<=65537,'Require prefill entry and nonempty decode trace')
    prompt=None
    for entry_no,entry in enumerate(raw):
        require(isinstance(entry,dict) and set(entry)=={str(i) for i in range(len(layers))},
                'Missing/extra source layer')
        for layer in layers:
            value=entry[str(layer['layer_id'])]
            if layer['kind']=='dense':
                require(value is None,'Dense source entry must be null');continue
            require(isinstance(value,list) and value and (entry_no==0 or len(value)==1),
                    'Decode source must contain one Token row')
            if entry_no==0:
                require(prompt is None or prompt==len(value),'Prefill Token count differs across layers');prompt=len(value)
            for choices in value:
                require(isinstance(choices,list) and len(choices)==layer['top_k'] and
                        all(type(e)is int and 0<=e<layer['num_experts'] for e in choices) and
                        len(set(choices))==len(choices),'Invalid source expert choices')
    require(prompt is not None, 'Trace has no routed layer')
    return prompt,len(raw)-1


def load_manifest(ref):
    _,manifest = read_json(ref)
    validate_manifest(manifest)
    layers,files=manifest['layers'],manifest['files']
    loaded={};paths=set();origins=set()
    for item in files:
        fields(item,'file_id original_path file format','route file')
        require(isinstance(item['file_id'],str) and item['file_id'] and item['file_id'] not in loaded and
                isinstance(item['original_path'],str) and item['original_path'], 'Duplicate/missing route file identity')
        require(item['format']=='token_layer_map_v1','Unverified/unsupported raw route layout')
        path,raw=read_json(item['file'])
        require(path not in paths and item['original_path'] not in origins,'Repeated raw route file')
        paths.add(path);origins.add(item['original_path'])
        prompt,_ = validate_raw(raw,layers)
        loaded[item['file_id']]=dict(raw=raw,prompt_tokens=prompt,available_decode_records=len(raw)-1,
            file=deepcopy(item['file']),original_path=item['original_path'])
    return manifest,loaded


def select_routes(ref, requests, models, selected_layers):
    manifest,loaded=load_manifest(ref)
    for model in models:
        require(model['name']==manifest['model']['name'] and model['hidden_size']==manifest['model']['hidden_dim'] and
                len(model['layers'])==manifest['model']['num_layers'],'Route model differs from execution')
        for actual,declared in zip(model['layers'],manifest['layers']):
            require(actual['kind']==declared['kind'] and actual['layer_id']==declared['layer_id'] and
                (actual['kind']=='dense' or all(actual[k]==declared[k] for k in ('num_experts','top_k'))),
                'Route layer structure/Top-K differs from execution')
    normalized=[];records={};selections=[];used=set()
    for request in requests:
        fields(request,'request_id rank_id arrival_ns file_id first_decode_entry decode_steps target_total_tokens','observed request')
        require(request['file_id'] in loaded and request['file_id'] not in used,'Unknown or reused source request file')
        used.add(request['file_id']);source=loaded[request['file_id']]
        first=integer(request['first_decode_entry'],'first source decode entry',1,source['available_decode_records'])
        steps=integer(request['decode_steps'],'observation steps',1,source['available_decode_records']-first+1)
        target=request['target_total_tokens'];end=source['prompt_tokens']+first+steps
        if target is not None:integer(target,'declared total target',end)
        routes=[]
        for entry in range(first,first+steps):
            layer_routes={}
            for layer_id in selected_layers:
                require(manifest['layers'][layer_id]['kind']=='moe','Cannot route a dense layer')
                experts=source['raw'][entry][str(layer_id)][0]
                index=source['prompt_tokens']+entry-1
                key=str(request['request_id']),index,layer_id
                require(key not in records,'Duplicate selected route')
                records[key]=dict(request_id=key[0],phase='decode',token_index=index,layer_id=layer_id,
                    expert_ids=deepcopy(experts),source_file=deepcopy(source['file']),file_id=request['file_id'],
                    source_entry=entry,source_layer_id=layer_id,source_token_row=0,
                    decode_index=entry-1,source_index_convention='entry_0_prefill_entry_1_first_decode')
                layer_routes[str(layer_id)]=deepcopy(experts)
            routes.append(layer_routes)
        normalized.append(dict(request_id=request['request_id'],rank_id=request['rank_id'],arrival_ns=request['arrival_ns'],
            prompt_tokens=source['prompt_tokens'],generated_tokens=first,decode_routes=routes))
        selections.append(dict(**deepcopy(request),prompt_tokens=source['prompt_tokens'],
            available_decode_records=source['available_decode_records'],
            reserved_kv_tokens=end-1,observed_tokens_reached=end,source_original_path=source['original_path']))
    evidence=dict(manifest=deepcopy(ref),source=manifest['source'],route_model=manifest['model'],selections=selections,
        route_data_is_synthetic=manifest['source']['kind']=='synthetic_test',
        target_precision_match=False,infer_eos_from_file_end=False,selection='explicit_file_and_contiguous_entry_range',
        expected_records=len(records))
    return normalized,records,evidence


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    ref=dict(path=str(args.manifest.resolve()),sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest())
    manifest,files=load_manifest(ref)
    require(not args.output_dir.exists(),'Output directory already exists')
    args.output_dir.mkdir(parents=True)
    count=0
    with (args.output_dir/'normalized_decode.jsonl').open('w') as output:
        for file_id,source in files.items():
            for entry in range(1,len(source['raw'])):
                for layer in manifest['layers']:
                    if layer['kind']=='dense':continue
                    record=dict(file_id=file_id,phase='decode',decode_index=entry-1,
                        token_index=source['prompt_tokens']+entry-1,layer_id=layer['layer_id'],
                        expert_ids=source['raw'][entry][str(layer['layer_id'])][0],
                        source_file=source['file'],source_entry=entry,source_token_row=0)
                    output.write(json.dumps(record)+'\n');count+=1
    report=dict(schema_version='moe-ub-route-import-report/1',manifest=ref,source=manifest['source'],model=manifest['model'],
        files=[dict(file_id=i,**{k:v for k,v in source.items() if k!='raw'}) for i,source in files.items()],
        records=count,raw_schema_verified=True,eos_inferred=False,
        real_model_trace_claim=manifest['source']['kind']!='synthetic_test',target_hardware_conditions_validated=False,
        calibration_status='unvalidated_reference',normalized_sha256=hashlib.sha256((args.output_dir/'normalized_decode.jsonl').read_bytes()).hexdigest())
    (args.output_dir/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Validated',len(files),'files and',count,'decode layer records; no EOS inference')


if __name__=='__main__':main()
