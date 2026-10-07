"""Prepare, freeze and execute the few declared full-scene replay comparisons."""
from pathlib import Path
import os
import shutil
import math

from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save
from .workflow import ARTIFACT_ROOTS, host, platform

FULL=Path(__file__).resolve().parent/'configs/full'


def storage_usage(roots):
    """Count apparent and allocated bytes once per inode across registered roots."""
    seen=set(); apparent=allocated=0; files=0
    for root in roots:
        for directory,dirs,names in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(directory)/d).is_symlink()]
            for path in [Path(directory),*(Path(directory)/n for n in names)]:
                info=path.lstat(); identity=info.st_dev,info.st_ino
                if path.is_symlink() or identity in seen:
                    continue
                seen.add(identity); allocated+=info.st_blocks*512
                if path.is_file():
                    apparent+=info.st_size; files+=1
    return dict(apparent_bytes=apparent,allocated_bytes=allocated,charged_bytes=max(apparent,allocated),unique_files=files)


def prepare_full(source, backend, output):
    source,backend,output=map(lambda p:Path(p).resolve(),(source,backend,output))
    require(not output.exists(),'Full preparation output exists')
    reference=read(FULL/'reference.json')
    require(digest(source/'slice.json')==reference['source_slice_sha256'],'Full source slice differs from the fixed audited reference')
    require(backend.is_file(),'Missing backend')
    output.mkdir(parents=True)
    platform(['slice','inspect','--slice',source,'--verify'],output,'01-source-verify')
    metadata=read(source/'slice.json'); deployment=read(source/'raw/deployment.json')
    require(metadata['ranks']==list(range(128)) and len(deployment['groups'])==1,'Full source rank/group mismatch')
    group=deployment['groups'][0]; model=group['model_spec']; layers=group['layer_configs']
    require(len(model['layers'])==61 and len(layers)==58 and model['hidden_size']==7168 and
            all(l['template']=='cann_a3_v850_native_v1' and l['top_k']==8 and len(l['expert_placement'])==256 for l in layers),
            'Full model/communication scope mismatch')
    shutil.copytree(FULL,output/'configuration')
    shutil.copytree(source/'inputs/network',output/'network')
    backend_config=read(output/'network/backend.json')
    require(backend_config['attributes']['ns3::UbLdstApi::UsePacketSpray']=='true', 'Full source must use native spray')
    backend_config['schema_version']='moe-ub-backend-config/3'
    backend_config['port_service']=dict(schema_version='moe-ub-port-service/1',execution_end_ns=reference['max_simulation_ns'],intervals=[])
    save(output/'network/backend.json',backend_config)
    policy=host(); policy.update(rss_bytes=48*2**30,address_bytes=56*2**30,wall_seconds=7200)
    config=dict(schema_version='moe-ub-replay/2',host=policy,export_host=host(),backend=str(backend),
        backend_timeout_seconds=7000,export_policy=str(output/'configuration/export-policy.json'),
        observation=str(output/'configuration/observation.json'),network=str(output/'network'),
        max_simulation_ns=reference['max_simulation_ns'],cache_directory=str(output/'graph-cache'))
    save(output/'replay.json',config)
    archives=[a for a in metadata['archives'] if a['kind']=='graph_archive']
    require(len(archives)==1,'Need one complete graph archive')
    usage=storage_usage([*ARTIFACT_ROOTS,output])
    graph=archives[0]['expanded_logical_bytes']; budget=reference['artifact_budget_bytes']
    # This is a preflight upper allowance, not a claim about measured future usage.
    reserve=reference['record_compressed_reserve_bytes']+reference['export_compressed_reserve_bytes']+reference['analysis_projection_reserve_bytes']
    footprint=usage['charged_bytes']+graph+reserve
    free=shutil.disk_usage(output).free
    report=dict(schema_version='mapping-value-full-preflight/1',simulation_executed=False,
        source_integrity_verified=True,source_slice=str(source),source_slice_sha256=digest(source/'slice.json'),backend_sha256=digest(backend),
        registered_storage=usage,graph_expansion_bytes=graph,new_record_export_analysis_reserve_bytes=reserve,
        artifact_budget_bytes=budget,minimum_graph_only_peak_bytes=usage['charged_bytes']+graph,
        reserved_peak_bytes=footprint,budget_shortfall_bytes=max(0,footprint-budget),
        graph_alone_shortfall_bytes=max(0,usage['charged_bytes']+graph-budget),
        free_filesystem_bytes=free,min_free_bytes=20*2**30,
        ready_to_run=footprint<=budget and free>=20*2**30+graph+reserve,
        next_command=['python','-m','moe_ub_cosim','replay','--slice',str(source),'--config',str(output/'replay.json'),'--output',str(output/'r000')],
        graph_cache='one explicitly shared cache for all cases; do not expand one copy per case',
        note='No old run, data package or cache has been deleted. A nonzero shortfall requires an authorized storage decision before full replay.')
    save(output/'preflight.json',report)
    return dict(passed=True,output=str(output),source_verified=True,simulation_executed=False,
                ready_to_run=report['ready_to_run'],budget_shortfall_bytes=report['budget_shortfall_bytes'])


def freeze_full(baseline, output):
    baseline,output=map(lambda p:Path(p).resolve(),(baseline,output))
    require(not output.exists(),'Frozen full cases already exist')
    source=read(baseline/'preflight.json'); run=baseline/'r000'
    manifest=read(run/'manifest.json'); spec=read(baseline/'configuration/reference.json')
    require(manifest['normal_exit'] and manifest['source_slice_sha256']==source['source_slice_sha256'],
            'Need complete normal replay of the prepared source')
    selected=[r for r in stored_rows(run/'truth','operation_registry.jsonl') if r['layer_id']==spec['target_layer']]
    require(len(selected)==2 and {r['kind'] for r in selected}=={'dispatch','combine'},'Full target layer missing')
    start=min(e['start_global_ns'] for op in selected for e in op['endpoint_records'])
    end=max(e['end_global_ns'] for op in selected for e in op['endpoint_records'])
    node,port=spec['target_port']
    captured=[p for p in read(run/'observed/public_config.json')['ports'] if (p['switch_id'],p['port_id'])==(node,port)]
    require(len(captured)==1,'Full target port must be observed')
    capture=captured[0]; period=capture['period_ns']; origin=capture['start_local_ns']
    left=origin+(start-origin)//period*period; right=origin+math.ceil((end-origin)/period)*period
    require(origin<=left<right<=capture['end_local_ns'],'Full interval outside observation coverage')
    output.mkdir(parents=True)
    cases={}
    for index,rate in enumerate((None,112_000_000_000,56_000_000_000)):
        name=f'r{index:03d}'
        value=dict(schema_version='mapping-value-case/1',case_id=name,
            normal_manifest_sha256=digest(run/'manifest.json'),endpoint_prepare=[],
            purpose='normal' if rate is None else 'physical_egress_service',
            port_service=[] if rate is None else [dict(node_id=node,port_id=port,start_ns=left,end_ns=right,rate_bps=rate)])
        save(output/(name+'.json'),value); cases[name]=digest(output/(name+'.json'))
    save(output/'freeze.json',dict(schema_version='mapping-value-freeze/1',normal_run=str(run),
        normal_manifest_sha256=digest(run/'manifest.json'),source_slice_sha256=source['source_slice_sha256'],
        selected_layer=spec['target_layer'],normal_interval_ns=[start,end],alignment_period_ns=period,
        cases=cases,rule='Declared port and rates, interval from normal all-participant layer span; no result-based retuning'))
    return dict(passed=True,output=str(output),cases=list(cases),interval_ns=[left,right])


def run_full_case(baseline, case_path, output):
    baseline,case_path,output=map(lambda p:Path(p).resolve(),(baseline,case_path,output))
    require(not output.exists(),'Full case output already exists')
    case=read(case_path); source=read(baseline/'preflight.json'); config=read(baseline/'replay.json')
    frozen=read(case_path.parent/'freeze.json')
    require(case['case_id'] in frozen['cases'] and frozen['cases'][case['case_id']]==digest(case_path),
            'Full case differs from frozen declaration')
    require(case['normal_manifest_sha256']==digest(baseline/'r000/manifest.json') and
            read(baseline/'r000/manifest.json')['normal_exit'], 'Complete normal reference changed')
    require(case['endpoint_prepare']==[] and len(case['port_service'])==1,'Full case must contain one network intervention')
    require(digest(Path(source['source_slice'])/'slice.json')==source['source_slice_sha256'] and
            digest(config['backend'])==source['backend_sha256'],'Full source or backend changed')
    # A completed normal replay materialized this cache. The platform verifies its
    # inventory again; a missing/partial cache is not silently re-expanded here.
    cache=Path(config['cache_directory'])
    require((cache/'graph_archive.ready.json').is_file(),'Shared normal graph cache is not ready')
    usage=storage_usage([*ARTIFACT_ROOTS,baseline,output,cache]); spec=read(baseline/'configuration/reference.json')
    reserve=sum(spec[k] for k in ('record_compressed_reserve_bytes','export_compressed_reserve_bytes','analysis_projection_reserve_bytes'))
    budget=min(config['host']['artifact_bytes'],config['export_host']['artifact_bytes'])
    output.mkdir(parents=True)
    ready=usage['charged_bytes']+reserve<=budget and shutil.disk_usage(output).free>=config['host']['min_free_bytes']+reserve
    save(output/'preflight.json',dict(schema_version='mapping-value-case-preflight/1',
        registered_storage=usage,additional_reserved_bytes=reserve,artifact_budget_bytes=budget,
        graph_cache_reused=str(cache),ready_to_run=ready))
    require(ready,'Full case resource preflight failed; retained evidence, no backend launched')
    save(output/'case.json',case)
    shutil.copytree(baseline/'network',output/'network')
    network=read(output/'network/backend.json'); network['port_service']['intervals']=case['port_service']
    save(output/'network/backend.json',network); config['network']=str(output/'network')
    save(output/'replay.json',config)
    platform(['replay','--slice',source['source_slice'],'--config',output/'replay.json','--output',output/'run'],output,'01-replay')
    platform(['validate','--run',output/'run'],output,'02-validate')
    return dict(passed=True,output=str(output),run='run',case_sha256=digest(case_path))
