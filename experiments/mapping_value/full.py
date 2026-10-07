"""Audit the immutable full input and prepare public replay commands, without running it."""
from pathlib import Path
import os
import shutil

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
