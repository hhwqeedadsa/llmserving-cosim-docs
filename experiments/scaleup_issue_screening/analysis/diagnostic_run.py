"""Prepare bounded passive captures/service references through public replay.

The source is an explicit sealed compact slice. This module never builds a
graph, changes native resources or uses a historical test fixture.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import sys
from .consumer_review import Archive
from .organization import equivalent
from .workflow import CONFIG,cli,digest,host,read,save


def prepare(archive,bundle,backend,rule,run_id,output):
    archive,bundle,backend,rule,output=map(lambda p:Path(p).resolve(),(archive,bundle,backend,rule,output))
    spec=read(rule);case=next(r for r in spec['primary_cases'] if r['id']==run_id)
    old=Archive(archive);manifest=read(bundle/'slice.json')
    if manifest['run_id']!=case['source']:raise ValueError('Run source differs from predeclared workload')
    if manifest.get('compaction',{}).get('source_slice_sha256')!=old.index['entries']['slice/slice.json']['sha256']:
        raise ValueError('Compact source does not match original archive')
    net=read(bundle/'inputs/network/backend.json');obs=read(bundle/'inputs/observation.json')
    if digest(bundle/'inputs/network/backend.json')!=old.index['entries']['run/inputs/network/backend.json']['sha256']:
        raise ValueError('Original network changed')
    if net.get('port_service',{}).get('intervals') or obs.get('native_queue'):
        raise ValueError('Expected original normal source')
    if net['attributes'].get('ns3::UbLdstThread::StoreQueuePolicy','fifo')!='fifo':
        raise ValueError('C1 is frozen at FIFO')
    links={}
    for l in net['topology']['links']:
        for a,b in (('src','dst'),('dst','src')):
            links[l[a],l[a+'_port']]=(l[b],l[b+'_port'])
    selected={(s['switch_id'],p) for s in obs['switches'] for p in s['port_ids']}
    # Both ends of every declared physical link. This includes source outlets;
    # unselected physical links remain explicitly outside this capture scope.
    selected|={links[k] for k in list(selected)}
    if len(selected)>4096:raise ValueError('Capture exceeds approved port cap')
    obs.update(schema_version='moe-ub-observation-config/6',network_facts=dict(
        schema_version='moe-ub-network-facts/1',ports=[dict(node_id=n,port_id=p) for n,p in sorted(selected)],
        end_ns=spec['capture_end_ns'],max_records=spec['max_fact_records']))
    deep=read(CONFIG/'scenarios/mechanism-cases.json');details=set()
    for c in deep['cases']:
        s=c['selection']
        details.update(map(tuple,s['outlets']) if 'outlets' in s else [(s['switch_id'],s['port_id'])])
    if not details<=selected:raise ValueError('Deep cases outside declared capture')
    obs['recording']['detail_ranges']=[dict(switch_id=n,port_id=p,start_ns=0,end_ns=spec['capture_end_ns']) for n,p in sorted(details)]
    changed=deepcopy(net)
    if case['service']:
        from moe_ub_cosim.port_service import validate
        changed.update(schema_version='moe-ub-backend-config/3',port_service=dict(
            schema_version='moe-ub-port-service/1',execution_end_ns=manifest['capture_end_ns'],intervals=case['service']))
        validate(changed['port_service'],changed['topology'])
    output.mkdir(parents=True,exist_ok=False)
    cli(output,'slice-verify','slice','inspect','--slice',bundle,'--verify')
    shutil.copytree(bundle/'inputs/network',output/'network');save(output/'network/backend.json',changed)
    save(output/'observation.json',obs)
    e=read(bundle/'inputs/effective.json')
    save(output/'replay.json',dict(schema_version='moe-ub-replay/2',host=host(True),export_host=host(),
        backend=str(backend),backend_timeout_seconds=e['backend_timeout_seconds'],max_simulation_ns=e['max_simulation_ns'],
        network=str(output/'network'),observation=str(output/'observation.json'),
        export_policy=str(CONFIG/'observation/export-policy.json'),diagnostic_storage='gzip'))
    save(output/'selection.json',dict(schema_version='issue-diagnostic-selection/1',mode='fifo',
        case=case,rule_sha256=digest(rule),original_archive=str(archive),original_archive_sha256=old.index['archive_sha256'],
        original_manifest_sha256=old.index['entries']['run/manifest.json']['sha256'],
        slice=str(bundle),slice_sha256=digest(bundle/'slice.json'),backend_sha256=digest(backend),
        program_sha256=digest(__file__),ports=len(selected),
        detail_cases_sha256=digest(CONFIG/'scenarios/mechanism-cases.json'),detail_ports=sorted(details),
        fixed=['graph','native requests','compute','FIFO','quotas','synchronization','topology','routing algorithm','random','credit geometry']))
    print('Prepared',run_id,len(selected),'ports')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','run','equivalent'))
    for n in ('archive','slice','backend'):p.add_argument('--'+n,type=Path)
    p.add_argument('--rule',type=Path,default=CONFIG/'scenarios/diagnostic-study.json')
    p.add_argument('--run-id');p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='run':
        s=read(a.output/'selection.json')
        if digest(Path(s['slice'])/'slice.json')!=s['slice_sha256'] or digest(read(a.output/'replay.json')['backend'])!=s['backend_sha256']:
            raise ValueError('Prepared source/backend changed')
        cli(a.output,'replay','replay','--slice',s['slice'],'--config',a.output/'replay.json','--output',a.output/'run')
        cli(a.output,'validate','validate','--run',a.output/'run')
    elif a.worker:
        if a.action=='prepare':prepare(a.archive,a.slice,a.backend,a.rule,a.run_id,a.output)
        else:
            if read(a.output/'selection.json')['case']['service']:raise ValueError('A control is not an equivalence run')
            equivalent(a.archive,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        result=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-'+a.action+'-resources',**host())
        if result['status']!='completed':raise SystemExit(result['status'])

if __name__=='__main__':main()
