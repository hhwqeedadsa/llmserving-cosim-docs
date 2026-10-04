"""Deterministic, bounded, file-at-a-time selection from a pinned route catalog.

This module has no network access. Missing earlier candidates cannot be skipped
in favor of locally available files. Output manifests contain metadata, not routes.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from .data_inputs import ROOT
from .model_capacity import fields
from .plan import integer, require
from .route_evidence import read_json, unique_object, validate_manifest, inspect_raw


def digest(raw):return hashlib.sha256(raw).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n');return path

def relative_path(value):
    require(isinstance(value,str) and value and '\\' not in value and '\x00' not in value, 'Invalid catalog path')
    p=PurePosixPath(value)
    require(not p.is_absolute() and '..' not in p.parts and p.as_posix()==value, 'Noncanonical catalog path')
    return value


def prepare(config):
    fields(config,'schema_version catalog source_manifest data_root selection budget','route selection')
    require(config['schema_version']=='moe-ub-route-selection/1','Unsupported route selection schema')
    _,catalog=read_json(config['catalog']);_,source=read_json(config['source_manifest']);validate_manifest(source)
    fields(catalog,'schema_version dataset_id dataset_revision prefix scope complete files pages checked_utc','route catalog')
    require(catalog['schema_version']=='moe-ub-route-catalog/1' and catalog['complete'] is True and
        catalog['scope']=='complete_fixed_revision_subtree','Require complete fixed-revision catalog, not a local subset')
    require(all(catalog[k]==source['source'][k] for k in ('dataset_id','dataset_revision')), 'Catalog/source revision mismatch')
    c=config['selection'];fields(c,'algorithm seed subset required_requests min_decode_records decode_steps rank_ids arrival_ns','selection policy')
    require(c['algorithm']=='sha256_seed_newline_path_v1','Unsupported deterministic selection algorithm')
    integer(c['seed'],'seed');integer(c['required_requests'],'required requests',1,4096)
    integer(c['min_decode_records'],'minimum records',1,65536)
    integer(c['decode_steps'],'decode steps',1,c['min_decode_records']);integer(c['arrival_ns'],'arrival')
    require(isinstance(c['subset'],str) and c['subset'] and '/' not in c['subset'], 'Invalid benchmark subset')
    require(catalog['prefix']==source['source']['collector_model_id']+'/'+c['subset']+'/', 'Catalog benchmark/model prefix mismatch')
    require(isinstance(c['rank_ids'],list) and c['rank_ids'] and len(c['rank_ids'])==len(set(c['rank_ids'])), 'Invalid selection rank list')
    for r in c['rank_ids']:integer(r,'rank',0,2**31-2)
    b=config['budget'];fields(b,'max_scan_files max_file_bytes max_total_read_bytes','selection budget')
    for k,v in b.items():integer(v,k,1)
    require(b['max_scan_files']>=c['required_requests'], 'Scan budget smaller than required requests')
    files=catalog['files'];require(isinstance(files,list) and files,'Empty catalog')
    seen=set()
    for row in files:
        require(isinstance(row,dict) and set(row) in ({'path','size','git_blob_id'},{'path','size','git_blob_id','lfs_sha256'}), 'Invalid catalog file')
        path=relative_path(row['path'])
        require(path.startswith(catalog['prefix']) and path.endswith('.json') and path not in seen,'Duplicate/out-of-subset catalog path');seen.add(path)
        integer(row['size'],'catalog bytes',1)
        require(isinstance(row['git_blob_id'],str) and re.fullmatch('[0-9a-f]{40}',row['git_blob_id']), 'Invalid Git blob identity')
        if 'lfs_sha256' in row:require(isinstance(row['lfs_sha256'],str) and re.fullmatch('[0-9a-f]{64}',row['lfs_sha256']), 'Invalid LFS identity')
    ordered=sorted(files,key=lambda r:(digest((str(c['seed'])+'\n'+r['path']).encode()),r['path']))
    require(isinstance(config['data_root'],str) and config['data_root'],'Missing local data root')
    return catalog,source,ordered


def download_plan(config):
    catalog,source,ordered=prepare(config);budget=config['budget'];rows=[];total=0
    for row in ordered[:budget['max_scan_files']]:
        require(row['size']<=budget['max_file_bytes'],'Candidate exceeds file budget: '+row['path'])
        if total+row['size']>budget['max_total_read_bytes']:break
        rows.append(row);total+=row['size']
    require(len(rows)>=config['selection']['required_requests'],'Candidate byte budget cannot cover required request count')
    return dict(schema_version='moe-ub-route-download-plan/1',catalog=config['catalog'],source_manifest=config['source_manifest'],
        dataset_id=catalog['dataset_id'],dataset_revision=catalog['dataset_revision'],prefix=catalog['prefix'],
        selection=config['selection'],budget=budget,data_root=config['data_root'],
        candidate_count=len(ordered),candidate_bytes=sum(r['size'] for r in ordered),
        bounded_candidates=rows,max_selected_candidate_bytes=total,eligibility_verified=False,
        stop_after_eligible=config['selection']['required_requests'],download_authorization_inferred=False)


def select(config, output):
    catalog,source,ordered=prepare(config)
    output.mkdir(parents=True,exist_ok=False)
    policy=config['selection'];budget=config['budget'];root=(ROOT/config['data_root']).resolve()
    report=dict(schema_version='moe-ub-route-selection-report/1',state='checking',data_ready=False,
        catalog=config['catalog'],source_manifest=config['source_manifest'],selection=policy,budget=budget,
        catalog_files=len(ordered),checked=[],selected_files=0,read_bytes=0,
        route_source=source['source'],eos_inferred=False,target_precision_match=False,target_scale_executed=False)
    chosen=[];requests=[];hashes=set();paths=set();manifest_paths=[]
    try:
        for row in ordered:
            if len(chosen)==policy['required_requests']:break
            require(len(report['checked'])<budget['max_scan_files'],'Scan budget exhausted before enough eligible files')
            require(row['size']<=budget['max_file_bytes'] and report['read_bytes']+row['size']<=budget['max_total_read_bytes'],
                    'Route read byte budget exceeded')
            report['current_candidate']=row['path']
            path=(root/row['path']).resolve()
            require(path.is_relative_to(root) and path not in paths,'Repeated/outside local source path')
            require(path.is_file(),'Missing earlier candidate; cannot substitute a later local file: '+row['path'])
            require(path.stat().st_size==row['size'],'Route/catalog size mismatch: '+row['path'])
            with path.open('rb') as stream:raw=stream.read(budget['max_file_bytes']+1)
            require(len(raw)==row['size'],'Route changed while reading')
            sha=digest(raw);report['read_bytes']+=len(raw)
            if 'lfs_sha256' in row:require(sha==row['lfs_sha256'],'Route/catalog LFS SHA256 mismatch')
            else:require(hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()==row['git_blob_id'],'Route/catalog Git blob mismatch')
            require(sha not in hashes,'Repeated raw content cannot establish an independent request')
            paths.add(path);hashes.add(sha)
            item=dict(original_path=row['path'],sha256=sha,bytes=row['size'])
            try:
                prompt,steps=inspect_raw(raw,source['layers'])
            except (ValueError,TypeError) as error:
                # Reject the entire source file. Never fill gaps or take only its valid prefix.
                del raw
                report['checked'].append(dict(item,raw_schema_valid=False,eligible=False,
                    reason='invalid_source_file',error=str(error)))
                continue
            del raw
            eligible=steps>=policy['min_decode_records']
            item.update(raw_schema_valid=True,prompt_tokens=prompt,available_decode_records=steps,eligible=eligible,
                        reason='selected' if eligible else 'too_few_decode_records')
            report['checked'].append(item)
            if not eligible:continue
            index=len(chosen);file_id=f'request-{index:04d}'
            local=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            chosen.append(dict(file_id=file_id,original_path=row['path'],file=dict(path=local,sha256=sha),format='token_layer_map_v1'))
            requests.append(dict(request_id=index+1,rank_id=policy['rank_ids'][index%len(policy['rank_ids'])],
                arrival_ns=policy['arrival_ns'],file_id=file_id,first_decode_entry=1,
                decode_steps=policy['decode_steps'],target_total_tokens=None))
            item.update(manifest_index=index,request_id=index+1,rank_id=requests[-1]['rank_id'],
                first_input_token_index=prompt,last_input_token_index=prompt+policy['decode_steps']-1,
                reserved_kv_tokens=prompt+policy['decode_steps'],observed_tokens_reached=prompt+policy['decode_steps']+1)
            report['selected_files']=len(chosen)
        require(len(chosen)==policy['required_requests'],'Insufficient eligible independent route files')
        manifest=deepcopy(source);manifest['files']=chosen
        manifest_path=save(output/'manifest.json',manifest);manifest_paths.append(manifest_path)
        # The request list has the existing evidence-service selection fields.
        request_path=save(output/'requests.json',requests);manifest_paths.append(request_path)
        manifest_ref=dict(path=str(manifest_path.resolve()),sha256=digest(manifest_path.read_bytes()))
        report.update(state='ready',data_ready=True,manifest=manifest_ref,requests_sha256=digest(request_path.read_bytes()),
            selected_decode_layer_records=len(chosen)*policy['decode_steps']*sum(l['kind']=='moe' for l in source['layers']),
            requests_per_rank=dict(sorted(Counter(r['rank_id'] for r in requests).items())))
        report.pop('current_candidate',None)
    except Exception as error:
        report.update(state='rejected',data_ready=False,error=str(error))
        # Only outputs just created by this call can be removed on a write failure.
        for path in manifest_paths:path.unlink()
        raise
    finally:save(output/'report.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','select'));parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();config=json.loads(args.input.read_text(),object_pairs_hook=unique_object)
    if args.action=='plan':
        plan=download_plan(config);args.output_dir.mkdir(parents=True,exist_ok=False)
        save(args.output_dir/'download-plan.json',plan)
        print('Bounded candidates:',len(plan['bounded_candidates']),'bytes:',plan['max_selected_candidate_bytes'],'eligibility unverified')
    else:
        report=select(config,args.output_dir)
        print('Validated independent files:',report['selected_files'],'selected layer records:',report['selected_decode_layer_records'])


if __name__=='__main__':main()
