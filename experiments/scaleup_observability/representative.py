"""A bounded normal/service pair on one audited full first-round slice."""
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import shutil
import sys

from .common import HERE, bounded, command, digest, host, read, require, save, sources
from .service_exploration import selected_ports
from .workflow import storage

SLICE_SHA = 'b90fb6150ca272af9f22552c092750e09148c199918f92c0950549b251714d07'
FULL_ROUND_NS = 48446098
MAX_NS = 100_000_000
RESERVE = 6*2**30


def observation(ports):
    switches=defaultdict(list)
    for node,port,_ in ports.values():switches[node].append(port)
    return dict(schema_version='moe-ub-observation-config/3',record_provenance=True,
        clocks=[dict(device_type=kind,device_id=i,offset_ns=0,drift_ppb=0)
                for kind,ids in [('rank',range(128)),('switch',sorted(switches))] for i in ids],
        switches=[dict(switch_id=n,port_ids=sorted(set(ps)),start_local_ns=0,end_local_ns=MAX_NS,period_ns=1000)
                  for n,ps in sorted(switches.items())],
        statistics=dict(schema_version='moe-ub-observation-statistics/1',queue_waiting_bytes=True,operation_stages=True),
        resource_budget=dict(schema_version='moe-ub-observation-resources/1',max_ports=3,
            max_windows_per_port=100000,max_total_windows=300000,max_event_bytes_per_port=64*2**20,
            max_ledger_packets=1000000,max_registered_transfers=5000000,max_export_bytes=2**30),
        recording=dict(schema_version='moe-ub-observation-recording/1',mode='aggregate',detail_ranges=[],
            truth_categories=['business_data','attached_metadata','dispatch_count','combine_flag','load_ready'],
            max_detail_events_per_port=500000,max_window_attribution_entries=4096))


def cache_binding(source, cache):
    meta=read(source/'slice.json')
    require(digest(source/'slice.json')==SLICE_SHA,'Unexpected full source slice')
    require(read(cache/'slice-binding.json')==dict(slice_sha256=SLICE_SHA),'Cache source differs')
    archive=next(a for a in meta['archives'] if a['kind']=='graph_archive')
    require(read(cache/'graph_archive.ready.json')==archive,'Existing complete graph cache required; no implicit expansion')


def policy(study, full=False):
    limits=host(study['output'])
    limits['artifact_roots'] += [study['source'],study['cache']]
    if full:limits.update(rss_bytes=48*2**30,address_bytes=56*2**30,wall_seconds=7200)
    return limits


def bounded_stage(meta, argv, directory, label):
    limits=policy(meta)
    args=[sys.executable,'-m','moe_ub_cosim.host_probe','--output-dir',Path(directory)/(label+'.resources')]
    for key in ('rss_bytes','address_bytes','artifact_bytes','wall_seconds','min_free_bytes'):
        args+=['--'+key.replace('_','-'),str(limits[key])]
    for root in limits['artifact_roots']:args+=['--artifact-root',root]
    command([*args,'--',*argv],directory,label)


def prepare(source, cache, backend, role, output):
    source,cache,backend,output=map(lambda p:Path(p).resolve(),(source,cache,backend,output))
    require(not output.exists(),'Representative output exists')
    require(backend.is_file(),'Missing backend')
    cache_binding(source,cache)
    meta=read(source/'slice.json');deployment=read(source/'raw/deployment.json')
    require(meta['normal_exit'] and meta['ranks']==list(range(128)) and len(meta['submissions'])==1 and
            meta['simulation_end_ns']==FULL_ROUND_NS and len(deployment['groups'])==1,'Full source scope differs')
    group=deployment['groups'][0];layers=group['layer_configs'];scope=meta['source']['historical_scope']
    require(len(group['model_spec']['layers'])==61 and len(layers)==58 and scope['requests']==1024 and
            all(l['template']=='cann_a3_v850_native_v1' and l['top_k']==8 and len(l['expert_placement'])==256 and
                l['send_slots_per_rank']==1 for l in layers),'Full workload differs')
    net=read(source/'inputs/network/backend.json');ports=selected_ports(net['topology'])
    require(role in ports and all(r==224_000_000_000 for _,_,r in ports.values()),'Invalid representative role/rate')
    output.mkdir(parents=True)
    bounded([sys.executable,'-m','moe_ub_cosim','slice','inspect','--slice',source,'--verify'],
            output/'commands','source-verify',output)
    save(output/'observation.json',observation(ports))
    save(output/'export-policy.json',dict(schema_version='moe-ub-research-export-policy/1',
        max_index_bytes=256*2**20,max_compressed_bytes=2*2**30,max_record_bytes=262144,
        max_endpoint_records=50000,max_operations=10000,max_layouts=10000,max_id_ranges=5000000,max_graphs=16,cache_rows=8192))
    node,port,rate=ports[role];interval=dict(node_id=node,port_id=port,start_ns=FULL_ROUND_NS//4,
        end_ns=3*FULL_ROUND_NS//4,rate_bps=rate//2)
    cases=[]
    for name,intervals in [('normal',[]),('service50',[interval])]:
        folder=output/'inputs'/name
        shutil.copytree(source/'inputs/network',folder/'network')
        changed=deepcopy(net);changed['schema_version']='moe-ub-backend-config/3'
        changed['port_service']=dict(schema_version='moe-ub-port-service/1',execution_end_ns=MAX_NS,intervals=intervals)
        save(folder/'network/backend.json',changed)
        cases.append(dict(case_id=name,port_service=intervals,
            files={str(p.relative_to(folder)):digest(p) for p in sorted(folder.rglob('*')) if p.is_file()}))
    method=HERE/'representative-method.md'
    save(output/'study.json',dict(schema_version='scaleup-representative-study/1',output=str(output),source=str(source),
        cache=str(cache),backend=str(backend),backend_sha256=digest(backend),source_slice_sha256=SLICE_SHA,
        source_scope=scope,representative_role=role,observed_ports=ports,cases=cases,implementation=sources(),
        method='experiments/scaleup_observability/representative-method.md',method_sha256=digest(method),
        fixed_files={p.name:digest(p) for p in (output/'observation.json',output/'export-policy.json')}))
    return dict(output=str(output),cases=[c['case_id'] for c in cases],role=role,simulation_executed=False)


def verify(study):
    study=Path(study).resolve();meta=read(study/'study.json')
    require(meta['schema_version']=='scaleup-representative-study/1' and Path(meta['output'])==study,'Invalid study')
    require([c['case_id'] for c in meta['cases']]==['normal','service50'],'Need fixed normal/service pair')
    ports=selected_ports(read(Path(meta['source'])/'inputs/network/backend.json')['topology'])
    require(meta['observed_ports']=={name:list(port) for name,port in ports.items()},'Selected full ports changed')
    node,port,rate=ports[meta['representative_role']]
    expected=[dict(node_id=node,port_id=port,start_ns=FULL_ROUND_NS//4,end_ns=3*FULL_ROUND_NS//4,rate_bps=rate//2)]
    require(meta['cases'][0]['port_service']==[] and meta['cases'][1]['port_service']==expected,'Full intervention changed')
    require(digest(meta['backend'])==meta['backend_sha256'],'Backend changed')
    require(all(digest(study/p)==sha for p,sha in meta['fixed_files'].items()),'Frozen observation/export changed')
    for c in meta['cases']:
        folder=study/'inputs'/c['case_id']
        require({str(p.relative_to(folder)):digest(p) for p in folder.rglob('*') if p.is_file()}==c['files'],'Frozen network changed')
    cache_binding(Path(meta['source']),Path(meta['cache']))
    return meta


def preflight(study, output):
    study,output=Path(study).resolve(),Path(output).resolve();meta=verify(study)
    require(not output.exists(),'Representative preflight exists')
    limits=policy(meta);usage=storage([Path(p) for p in limits['artifact_roots']]);free=shutil.disk_usage(study).free
    ready=usage['charged_bytes']+RESERVE<=limits['artifact_bytes'] and free-RESERVE>=limits['min_free_bytes']
    save(output,dict(schema_version='scaleup-representative-preflight/1',simulation_executed=False,
        storage=usage,additional_reserve_bytes=RESERVE,artifact_limit_bytes=limits['artifact_bytes'],
        min_free_bytes=limits['min_free_bytes'],free_bytes=free,ready=ready,shared_cache=meta['cache'],
        basis='One next run: 2 GiB records + 2 GiB export + 2 GiB analysis; later case is conditional on a new preflight'))
    require(ready,'Representative resource preflight failed; no cap change, cleanup, expansion or automatic retry')
    return read(output)


def run_case(study, case_id):
    study=Path(study).resolve();meta=verify(study)
    cases=[c for c in meta['cases'] if c['case_id']==case_id];require(len(cases)==1,'Unknown case')
    root=study/'runs'/case_id;require(not root.exists(),'Representative case exists; no failed-run retry')
    if case_id!='normal':
        baseline=read(study/'runs/normal/complete.json')
        require(baseline['passed'] and baseline['round_ns']==FULL_ROUND_NS and
                baseline['manifest_sha256']==digest(study/'runs/normal/run/manifest.json'),'Complete unchanged normal reference required')
    root.mkdir(parents=True);preflight(study,root/'preflight.json')
    config=dict(schema_version='moe-ub-replay/2',host=policy(meta,True),export_host=policy(meta),
        backend=meta['backend'],backend_timeout_seconds=7000,export_policy=str(study/'export-policy.json'),
        observation=str(study/'observation.json'),network=str(study/'inputs'/case_id/'network'),
        max_simulation_ns=MAX_NS,cache_directory=meta['cache'])
    save(root/'replay.json',config)
    command([sys.executable,'-m','moe_ub_cosim','replay','--slice',meta['source'],'--config',root/'replay.json',
             '--output',root/'run'],root/'commands','replay')
    bounded_stage(meta,[sys.executable,'-m','moe_ub_cosim','validate','--run',root/'run'],root/'commands','validate')
    bounded_stage(meta,[sys.executable,'-m','experiments.scaleup_observability.representative_analysis',
             study,case_id,root/'analysis'],root/'commands','analyze')
    summary=read(root/'analysis/summary.json')
    if case_id=='normal':require(summary['round_ns']==FULL_ROUND_NS,'Fresh normal did not reproduce fixed source time')
    save(root/'complete.json',dict(passed=True,case_id=case_id,round_ns=summary['round_ns'],
        manifest_sha256=digest(root/'run/manifest.json'),summary_sha256=digest(root/'analysis/summary.json')))
    return read(root/'complete.json')


def compare(study, output):
    study,output=Path(study).resolve(),Path(output).resolve();meta=verify(study)
    require(not output.exists(),'Representative comparison exists')
    rows=[];platform=[]
    for case in meta['cases']:
        root=study/'runs'/case['case_id'];done=read(root/'complete.json')
        require(done['passed'] and digest(root/'run/manifest.json')==done['manifest_sha256'] and
                digest(root/'analysis/summary.json')==done['summary_sha256'],'Completed evidence changed')
        rows.append(dict(case=case,completion=done,summary=read(root/'analysis/summary.json')))
        platform.append(read(root/'run/manifest.json')['software']['source_hashes'])
    require(platform[0]==platform[1],'Platform differs across full cases')
    a,b=[r['summary'] for r in rows]
    for k in ('transfers','local_deliveries','nodes','compute_work_rank_ns','actual_native_sizes','group_counts',
              'network_semantics_sha256','task_payload_bytes'):
        require(a[k]==b[k],f'Full executed work changed: {k}')
    save(output,dict(schema_version='scaleup-representative-comparison/1',access='researcher_internal',
        study_sha256=digest(study/'study.json'),source_slice_sha256=SLICE_SHA,backend_sha256=meta['backend_sha256'],
        method_sha256=meta['method_sha256'],cases=rows,round_delta_ns=b['round_ns']-a['round_ns'],
        round_change_fraction=(b['round_ns']-a['round_ns'])/a['round_ns'],
        queue_area_delta_byte_ns=b['observed_queue_area_byte_ns']-a['observed_queue_area_byte_ns'],
        checks=dict(same_slice_and_graph=True,same_platform=True,same_executed_work=True,complete_execution=True),
        qualification='Three topology-selected ports, aggregate observations; original 4096 B payloads and one send slot; no scale-only or whole-fabric attribution'))
    return dict(passed=True,output=str(output))
