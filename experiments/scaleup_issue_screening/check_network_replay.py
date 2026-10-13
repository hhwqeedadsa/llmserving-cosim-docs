"""Fresh synthetic public workflow for passive capture and one faster outlet.

Functional validation only; these generated data never enter normal screening.
"""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import shutil
import sys

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from .network_control import change_service
from .path_replay import row_digest
from .workflow import ROOT, cli, digest, host, read, save


def check(output, backend, resume=False):
    output=output.resolve();backend=backend.resolve();output.mkdir(parents=True,exist_ok=resume)
    example=ROOT/'examples/a3_segment'
    if not resume:
        cli(output,'resolve','resolve','--config',example/'scenario.yaml','--backend',backend,'--output',output/'resolved')
        source=read(example/'source-run.json')
        source.update(host=host(extra_roots=[output]),backend=str(backend))
        source['inputs']={k:str(output/'resolved'/(k+'.json' if k!='network' else k)) for k in source['inputs']}
        for k in ('observation','record_policy','export_policy'):source[k]=str(example/source[k])
        save(output/'source-run.json',source)
        spec=read(example/'segment.json');spec['source_run']='source-run.json';save(output/'segment.json',spec)
        cli(output,'prepare','segment','prepare','--config',output/'segment.json','--output',output/'prepared')
        cli(output,'baseline','run','--config',output/'prepared/run.json','--output',output/'baseline')
        cli(output,'baseline-validate','validate','--run',output/'baseline')
        cli(output,'freeze','segment','freeze','--from-run',output/'baseline','--output',output/'slice','--storage','hardlink')
        observation=read(output/'baseline/inputs/observation.json')
        observation['recording']['detail_ranges']=[dict(switch_id=s['switch_id'],port_id=p,start_ns=0,end_ns=1000000)
            for s in observation['switches'] for p in s['port_ids']]
        save(output/'detail.json',observation)
        replay=dict(schema_version='moe-ub-replay/2',host=host(extra_roots=[output]),export_host=host(extra_roots=[output]),
            backend=str(backend),backend_timeout_seconds=240,max_simulation_ns=1000000,
            observation=str(output/'detail.json'),export_policy=str(example/'export-policy.json'))
        save(output/'replay.json',replay)
        cli(output,'capture','replay','--slice',output/'slice','--config',output/'replay.json','--output',output/'capture')
        cli(output,'capture-validate','validate','--run',output/'capture')
    original=output/'baseline/raw/execution';captured=output/'capture/raw/execution'
    report=read(original/'result.json');equivalence={}
    for name in report['record_storage']['fields']:
        before=row_digest(iter_result(original,name));after=row_digest(iter_result(captured,name))
        if before!=after:raise ValueError('Small capture changed '+name)
        equivalence[name]=before
    for name in read(output/'baseline/observed/storage.json')['fields']:
        if row_digest(stored_rows(output/'baseline/observed',name))!=row_digest(stored_rows(output/'capture/observed',name)):
            raise ValueError('Small capture changed observed '+name)
    for folder in (original/'ports').iterdir():
        if read(folder/'observation-audit.json')!=read(captured/'ports'/folder.name/'observation-audit.json'):
            raise ValueError('Small capture changed packet content/path/time')
    network=read(output/'slice/inputs/network/backend.json');kinds={n['node_id']:n['kind'] for n in network['topology']['nodes']}
    candidate=next(dict(switch_id=l[s],port_id=l[s+'_port']) for l in network['topology']['links']
        for s,t in (('src','dst'),('dst','src')) if kinds[l[s]]!='DEVICE' and kinds[l[t]]=='DEVICE')
    changed=change_service(network,candidate,0,1000000,800000000000)
    attempt='faster-retry' if resume else 'faster'
    network_dir=output/('network-retry' if resume else 'network')
    replay=read(output/'replay.json')
    shutil.copytree(output/'slice/inputs/network',network_dir)
    save(network_dir/'backend.json',changed);replay['network']=str(network_dir);save(output/(attempt+'.json'),replay)
    cli(output,attempt,'replay','--slice',output/'slice','--config',output/(attempt+'.json'),'--output',output/attempt)
    cli(output,attempt+'-validate','validate','--run',output/attempt)
    after=read(output/attempt/'raw/execution/result.json')
    if row_digest(iter_result(original,'planned_transfers'))!=row_digest(iter_result(output/attempt/'raw/execution','planned_transfers')):
        raise ValueError('Service control changed planned traffic')
    costs=lambda path:row_digest({k:r[k] for k in ('graph_id','rank_id','node_id')}|dict(duration_ns=r['end_ns']-r['start_ns'])
        for r in iter_result(path,'node_execution') if r['kind']=='COMP')
    if costs(original)!=costs(output/attempt/'raw/execution'):raise ValueError('Service control changed compute cost')
    effective=read(output/attempt/'raw/execution/effective_backend.json')['live_port_service']
    if len(effective['intervals'])!=1 or effective['intervals'][0]['rate_bps']!=800000000000:
        raise ValueError('Directed service object did not receive requested rate')
    # Use only these freshly generated public runs for the all-rank reader.
    # Preserve real case/manifest/slice identities; no historical target fixture.
    from .archive import pack
    from .network_timing import compare_all
    cases=[output/'timing-baseline',output/'timing-treatment'];reviews=[]
    for case, run in zip(cases,(output/'capture',output/attempt)):
        case.mkdir()
        shutil.copytree(run,case/'run',copy_function=os.link)
        save(case/'case.json',dict(schema_version='issue-screening-case/1',owned_by='scaleup_issue_screening',synthetic_only=True))
        shutil.copyfile(output/'detail.json',case/'observation.json')
    source=pack(cases[0],output/'timing-source.tar.gz')
    plans={str(p.relative_to(output/'slice')):digest(p) for p in (output/'slice/raw/graphs').glob('*/plan.json')}
    for case in cases:
        save(case/'selection.json',dict(slice=str(output/'slice'),slice_sha256=digest(output/'slice/slice.json'),
            original_archive_sha256=source['archive_sha256']))
        reviews.append(dict(selection_sha256=digest(case/'selection.json'),manifest_sha256=digest(case/'run/manifest.json'),plan_files=plans))
    timing=compare_all(*cases,*reviews)
    if not timing['endpoint_operations']['count'] or not timing['model_stages']['count']:
        raise ValueError('Fresh all-rank reader omitted the synthetic workload')
    save(output/'all-rank-timing.json',timing)
    save(output/'summary.json',dict(passed=True,synthetic_only=True,no_old_run_input=True,
        backend_sha256=digest(backend),program_sha256=digest(__file__),capture_equivalence=equivalence,
        directed_service_readback=effective,baseline_end_ns=report['simulation_end_ns'],
        faster_end_ns=after['simulation_end_ns'],faster_is_not_a_required_test_result=True,
        all_rank_timing_sha256=digest(output/'all-rank-timing.json'),
        all_rank_endpoint_pairs=timing['endpoint_operations']['count'],all_rank_model_stage_pairs=timing['model_stages']['count']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--resume-faster',action='store_true',help='Reuse this check’s unchanged baseline/capture after a service-input error')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:check(a.output,a.backend,a.resume_faster)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+('-retry-resources' if a.resume_faster else '-resources'),**host(extra_roots=[a.output]))
        if r['status']!='completed':raise SystemExit('Small network workflow failed: '+r['status'])


if __name__=='__main__':main()
