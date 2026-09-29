"""Run the existing service and export the bounded M5 research observation contract."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from .observation import (ATTRIBUTION_FIELDS, PORT_FIELDS, backend_observation, integer, json_lines,
                          read_csv, require, validate_observation, validate_observed)
from .research_export import export_records, read, write_export
from .service import ROOT, run as run_service, save, service_groups

NETWORK_FILES=('network_attribute.txt','node.csv','topology.csv','routing_table.csv','transport_channel.csv')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(config):
    require(set(config)=={'schema_version','run_id','mode','execution','inputs','observation','max_simulation_ns','seed'} and
        config['schema_version']=='moe-ub-research-run/1' and config['mode'] in ('fixture','research') and
        config['execution']=={'entry_mode':'service'},'Unsupported research run fields/mode/entry')
    require(isinstance(config['run_id'],str) and re.fullmatch(r'[A-Za-z0-9_.-]+',config['run_id']),'Invalid run identity')
    require(type(config['seed']) is int and config['seed']==10,'This backend uses seed 10')
    integer(config['max_simulation_ns'],'max_simulation_ns',1)
    sources=config['inputs']
    require(set(sources)=={'service','layer','system','network'} and
            all(isinstance(v,str) and v for v in sources.values()),'Invalid input paths')
    paths={k:(ROOT/v).resolve() for k,v in sources.items()}
    require(all(paths[k].is_file() for k in ('service','layer','system')) and paths['network'].is_dir(),'Missing run input')
    require(all((paths['network']/name).is_file() for name in NETWORK_FILES),'Missing network input file')
    service,layer=read(paths['service']),read(paths['layer'])
    ranks=sorted(r for g in service_groups(service,layer) for r in g['rank_ids'])
    require(ranks==list(range(len(ranks))) and len(ranks) in (4,8,12),'Invalid service ranks')
    validate_observation(config['observation'],ranks,config['max_simulation_ns'])
    return paths,ranks


def project_facts():
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    sources=list((ROOT/'python/moe_ub_cosim').glob('*.py'))
    sources += [p for p in (ROOT/'backend').iterdir() if p.suffix in ('.cc','.hh','.cmake') or p.name=='CMakeLists.txt']
    dependencies=[]
    for line in git('submodule','status','--recursive').splitlines():
        parts=line.strip().split();dependencies.append(dict(commit=parts[0].lstrip('-+U'),path=parts[1]))
    return dict(project_commit=git('rev-parse','HEAD'),workspace_dirty=bool(git('status','--porcelain')),
        source_hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(sources)},dependencies=dependencies,
        dependency_lock_sha256=sha(ROOT/'deps.lock.json'))


def snapshot_inputs(config, paths, directory):
    save(directory/'run.json',config)
    for name in ('service','layer','system'):shutil.copyfile(paths[name],directory/(name+'.json'))
    service=read(directory/'service.json');service['run_id']=config['run_id'];save(directory/'service.json',service)
    (directory/'network').mkdir()
    for name in NETWORK_FILES:shutil.copyfile(paths['network']/name,directory/'network'/name)
    # Keep referenced evidence with the run as well as the original path/hash.
    refs={}
    if service.get('schema_version')=='moe-ub-data-service/1':
        refs['model.json']=service['model_file']['path'];refs['routes.jsonl']=service['routes']['file']['path']
        if 'file' in service['compute']:refs['compute.csv']=service['compute']['file']['path']
    elif 'model_config' in service:refs['model.json']=service['model_config']
    elif 'model' in service:refs['model.json']='third_party/LLMServingSim/configs/model/'+service['model']+'.json'
    if refs:
        (directory/'materials').mkdir()
        for name,path in refs.items():shutil.copyfile((ROOT/path).resolve(),directory/'materials'/name)


def validate_run(directory):
    directory=Path(directory).resolve();manifest=read(directory/'manifest.json')
    require(manifest['schema_version']=='moe-ub-research-manifest/1' and manifest['normal_exit'],
            'Run is not a completed research export')
    for name,digest in manifest['artifact_hashes'].items():
        path=directory/name
        require(path.resolve().is_relative_to(directory) and path.is_file() and sha(path)==digest,
                'Missing or changed run artifact: '+name)
    observed=validate_observed(directory/'observed')
    config=read(directory/'inputs/run.json');raw=directory/'logs/raw-run'
    ranks=sorted(r for g in read(raw/'deployment.json')['groups'] for r in g['rank_ids'])
    records=export_records(raw,config['observation'],ranks,config['max_simulation_ns'])
    require(read(directory/'observed/public_config.json')==records['public'] and
            json_lines(directory/'observed/endpoint_ops.jsonl')==records['endpoints'] and
            read_csv(directory/'observed/port_windows.csv',PORT_FIELDS)==records['counters'],
            'Observed output differs from original execution records')
    require(json_lines(directory/'truth/operation_registry.jsonl')==records['operations'] and
            json_lines(directory/'truth/transfer_registry.jsonl')==records['transfers'] and
            read_csv(directory/'truth/attribution_windows.csv',ATTRIBUTION_FIELDS,('operation_id','direction'))==records['attribution'] and
            read(directory/'truth/clock_models.json')==records['clocks'],'Truth differs from original execution records')
    records['validation']['observed_contract']=observed
    require(read(directory/'metrics/validation.json')==records['validation'],'Validation record mismatch')
    return records['validation']


def run(config_path, backend, output, timeout=60):
    config=read(config_path);paths,ranks=prepare(config)
    require(backend.is_file(),'Missing backend executable')
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    for name in ('inputs','logs','metrics'):(output/name).mkdir()
    manifest=dict(schema_version='moe-ub-research-manifest/1',run_id=config['run_id'],mode=config['mode'],
        entry_mode='service',normal_exit=False,time_unit='ns',byte_unit='byte',calibration_status='unvalidated_reference',
        **project_facts(),backend_sha256=sha(backend),started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        observation_access_boundary='observed_only',clock_models='truth/clock_models.json',
        record_provenance=config['observation']['record_provenance'])
    save(output/'manifest.json',manifest)
    try:
        snapshot_inputs(config,paths,output/'inputs')
        capture=backend_observation(config['observation'],ranks,config['max_simulation_ns'])
        save(output/'inputs/backend-observation.json',capture)
        raw=output/'logs/raw-run'
        args=SimpleNamespace(input=output/'inputs/service.json',layer=output/'inputs/layer.json',backend=backend,
            system=output/'inputs/system.json',network=output/'inputs/network',observation=output/'inputs/backend-observation.json',
            output_dir=raw,max_simulation_ns=config['max_simulation_ns'],wall_timeout_seconds=timeout)
        run_service(args)
        report=read(raw/'execution/result.json');service_manifest=read(raw/'manifest.json')
        require(report['random']==dict(seed=config['seed'],run=1),'Backend random settings differ from declared seed/run')
        shutil.copytree(raw/'graphs',output/'graphs')
        records=export_records(raw,config['observation'],ranks,config['max_simulation_ns'])
        write_export(output,records)
        save(output/'metrics/service_metrics.json',dict(schema_version='moe-ub-service-metrics/1',
            metrics_scope=service_manifest.get('latency_scope',service_manifest['compute_scope']),
            result=read(raw/'service_result.json')))
        plans=[read(p) for p in sorted((raw/'graphs').glob('*/plan.json'))]
        manifest.update(random=report['random'],sender_completion=report['sender_completion'],
            simulation_end_ns=report['simulation_end_ns'],counter_location='egress_packet_start',
            counting_positions=[{k:p[k] for k in ('switch_id','port_id','direction')} for p in records['public']['ports']],
            byte_semantics='ns3_packet_get_size_excludes_unmodeled_physical_overhead',
            routing_source=service_manifest['routing_source'],kv_source=service_manifest['kv_source'],
            compute_scope=service_manifest['compute_scope'],
            compute_sources=sorted({p.get('compute_source','none') for p in plans}),
            compute_data_evidence='logs/raw-run/input_evidence.json' if (raw/'input_evidence.json').exists() else 'inputs/layer.json',
            communication_templates=sorted({p['communication_template'] for p in plans}),
            communication_profile_basis='reference_model',communication_calibration_status='unvalidated_reference',
            chunk_bytes=sorted({p['input']['chunk_bytes'] for p in plans}),
            send_slots_per_rank=sorted({p['send_slots_per_rank'] for p in plans}),normal_exit=True)
        manifest['artifact_hashes']={str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*'))
                                    if p.is_file() and p!=output/'manifest.json'}
        manifest['input_hashes']={k:v for k,v in manifest['artifact_hashes'].items() if k.startswith('inputs/')}
        manifest['finished_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        save(output/'manifest.json',manifest)
        validate_run(output)
    except Exception as error:
        manifest.update(normal_exit=False,error=str(error),finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(output/'manifest.json',manifest)
        save(output/'metrics/validation.json',dict(schema_version='moe-ub-research-validation/1',passed=False,error=str(error)))
        raise
    print('Research export complete:',output)


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    execute=commands.add_parser('run');execute.add_argument('--config',type=Path,required=True)
    execute.add_argument('--backend',type=Path,required=True);execute.add_argument('--output-dir',type=Path,required=True)
    execute.add_argument('--wall-timeout-seconds',type=int,default=60)
    validate=commands.add_parser('validate-run');validate.add_argument('--run-dir',type=Path,required=True)
    observed=commands.add_parser('validate-observed');observed.add_argument('--observed-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='run':
        integer(args.wall_timeout_seconds,'wall timeout',1,3600)
        run(args.config.resolve(),args.backend.resolve(),args.output_dir,args.wall_timeout_seconds)
    elif args.command=='validate-run':print(json.dumps(validate_run(args.run_dir),indent=2))
    else:print(json.dumps(validate_observed(args.observed_dir),indent=2))


if __name__=='__main__':main()
