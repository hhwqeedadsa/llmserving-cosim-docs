"""Bounded normal-input port capture and service controls via public replay.

Preparation accepts an explicit sealed compact slice. It preserves its graph,
native resources, synchronization, topology and random settings.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import sys

from .consumer_review import Archive
from .organization import equivalent
from .workflow import CONFIG, ROOT, cli, digest, host, read, save


def capture_ports(topology, candidates):
    kinds={n['node_id']:n['kind'] for n in topology['nodes']}
    selected={(c['switch_id'],c['port_id']) for c in candidates}
    switches={n for n,p in selected};upstream=set();direct=[]
    for link in topology['links']:
        for src,dst in (('src','dst'),('dst','src')):
            if link[dst] not in switches:continue
            if kinds[link[src]]=='DEVICE':
                direct.append(dict(node_id=link[src],port_id=link[src+'_port'],target=link[dst]))
            else:upstream.add((link[src],link[src+'_port']))
    return sorted(selected|upstream),direct


def change_service(network, candidate, start, end, rate):
    value=deepcopy(network)
    if network.get('port_service',{}).get('intervals'):raise ValueError('Expected normal unmodified port service')
    point=candidate['switch_id'],candidate['port_id']
    links=[l for l in network['topology']['links'] for side in ('src','dst')
           if (l[side],l[side+'_port'])==point]
    if len(links)!=1 or rate<=links[0]['rate_bps']:raise ValueError('Service diagnostic must improve one real directed outlet')
    from moe_ub_cosim.port_service import validate
    value['schema_version']='moe-ub-backend-config/3'
    value['port_service']=dict(schema_version='moe-ub-port-service/1',
        execution_end_ns=end if 'port_service' not in value else value['port_service']['execution_end_ns'],
        intervals=[dict(node_id=point[0],port_id=point[1],start_ns=start,end_ns=end,rate_bps=rate)])
    validate(value['port_service'],value['topology'])
    return value


def prepare(archive, bundle, selection, backend, output, candidate=None, baseline_review=None):
    archive,bundle,selection,backend,output=map(lambda p:Path(p).resolve(),(archive,bundle,selection,backend,output))
    if output.exists():raise ValueError('Control output must be new')
    accepted=read(CONFIG/'controls/network-screening.json')
    if digest(backend)!=accepted['backend_sha256']:raise ValueError('Expected the accepted FIFO-preserving binary')
    if subprocess.run(['git','diff','--quiet',accepted['platform_commit'],'--','python/moe_ub_cosim',
            'backend','deps.lock.json','third_party/LLMServingSim','third_party/ns-3-ub'],cwd=ROOT).returncode:
        raise ValueError('Platform differs from accepted implementation')
    source=Archive(archive);manifest=read(bundle/'slice.json');chosen=read(selection)
    prior=read(baseline_review) if baseline_review else None
    if candidate and prior is None:raise ValueError('A service control requires the actual baseline gate review')
    if prior and (prior['backend_sha256']!=digest(backend) or
                  any(digest(bundle/k)!=v for k,v in prior['plan_files'].items())):
        raise ValueError('Baseline review must use the same binary and graph')
    if manifest.get('compaction',{}).get('source_slice_sha256')!=source.index['entries']['slice/slice.json']['sha256']:
        raise ValueError('Compact slice is not bound to the explicit original archive')
    if any(chosen['sources'][c['case_id']]['archive_sha256']!=source.index['archive_sha256'] for c in chosen['candidates']):
        raise ValueError('Selection differs from replay workload')
    network=read(bundle/'inputs/network/backend.json');observation=read(bundle/'inputs/observation.json')
    if digest(bundle/'inputs/network/backend.json')!=source.index['entries']['run/inputs/network/backend.json']['sha256']:
        raise ValueError('Network differs from original run')
    if observation.get('native_queue') or network['attributes'].get('ns3::UbLdstThread::StoreQueuePolicy','fifo')!='fifo':
        raise ValueError('Expected normal FIFO reference without extra native queue capture')
    ports,direct=capture_ports(network['topology'],chosen['candidates'])
    # Derive the bounded interval from the already fixed event groups, never
    # from the eventual outcome. Time zero retains all previous blockers.
    service_start=max(0,min(c['start_ns'] for c in chosen['candidates'])-2000)
    detail_end=max(c['end_ns'] for c in chosen['candidates'])+7000
    if detail_end>manifest['capture_end_ns']:raise ValueError('Selected interval exceeds slice capture budget')
    observation['recording']['detail_ranges']=[dict(switch_id=n,port_id=p,start_ns=0,end_ns=detail_end) for n,p in ports]
    intervention=None
    if candidate:
        c=next(c for c in chosen['candidates'] if c['candidate_id']==candidate)
        network=change_service(network,c,service_start,detail_end,800000000000)
        network['port_service']['execution_end_ns']=manifest['capture_end_ns']
        intervention=network['port_service']
    output.mkdir(parents=True)
    cli(output,'slice-verify','slice','inspect','--slice',bundle,'--verify')
    save(output/'observation.json',observation)
    shutil.copytree(bundle/'inputs/network',output/'network')
    save(output/'network/backend.json',network)
    effective=read(bundle/'inputs/effective.json')
    save(output/'replay.json',dict(schema_version='moe-ub-replay/2',host=host(True,[output,bundle]),
        export_host=host(extra_roots=[output,bundle]),backend=str(backend),
        backend_timeout_seconds=effective['backend_timeout_seconds'],max_simulation_ns=effective['max_simulation_ns'],
        network=str(output/'network'),observation=str(output/'observation.json'),export_policy=str(CONFIG/'export-policy.json'),
        diagnostic_storage='gzip'))
    save(output/'selection.json',dict(schema_version='issue-network-control-selection/1',mode='fifo',
        original_archive=str(archive),original_archive_sha256=source.index['archive_sha256'],
        original_manifest_sha256=source.index['entries']['run/manifest.json']['sha256'],
        slice=str(bundle),slice_sha256=digest(bundle/'slice.json'),backend_sha256=digest(backend),
        discovery=chosen,selection_sha256=digest(selection),ports=ports,
        direct_device_ingress_without_upstream_packet_capture=direct,
        detail_start_ns=0,detail_end_ns=detail_end,intervention=intervention,
        intervention_candidate=candidate,program_sha256=digest(__file__),
        baseline_review_sha256=digest(baseline_review) if prior else None,
        baseline_actual_last={o['operation_id']:o['all_actual_last'] for o in prior['operations']} if prior else {},
        fixed=['graph','requests','compute','native resources','FIFO','synchronization','topology','flow control','random'],
        hypotheses=[
            'If this outlet delays a necessary delivery or its feedback, faster local service should reduce that wait; complete dependency review tests business propagation.',
            'If later status, other planes, source work or fixed reordering dominates, local queue reduction need not advance the final operation.',
            'If downstream credit gates service, rate change alone need not test the mechanism; busy/idle and observed arrival evidence must be checked first.'],
        boundaries=['Service changes serialization and queueing together; total effect is not pure removable queue delay.',
                    'Shared execution can change packet paths and future injection; same seed does not imply same path.',
                    'A normal 256-request auxiliary case does not establish the 1024-request discovery effect.']))
    save(output/'case.json',dict(schema_version='issue-screening-case/1',owned_by='scaleup_issue_screening',
        case_id='network-'+(candidate or 'capture'),platform_commit=accepted['platform_commit'],backend_sha256=digest(backend)))
    print('Prepared',len(ports),'ports; intervention',candidate)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','run','equivalent'))
    for n in ('archive','slice','selection','backend','baseline-review'):p.add_argument('--'+n,type=Path)
    p.add_argument('--candidate',choices=('N1','N2'));p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='run':
        selected=read(a.output/'selection.json')
        cli(a.output,'replay','replay','--slice',selected['slice'],'--config',a.output/'replay.json','--output',a.output/'run')
    elif a.worker:
        if a.action=='prepare':prepare(a.archive,a.slice,a.selection,a.backend,a.output,a.candidate,a.baseline_review)
        else:
            if read(a.output/'selection.json')['intervention'] is not None:raise ValueError('Intervention is not an equivalence capture')
            equivalent(a.archive,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],
            str(a.output.resolve())+'-'+a.action+'-resources',**host(extra_roots=[a.output]))
        if r['status']!='completed':raise SystemExit('Network control '+a.action+' failed: '+r['status'])


if __name__=='__main__':main()
