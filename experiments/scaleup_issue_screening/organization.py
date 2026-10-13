"""Fixed-budget C1 queue control using a sealed slice and public replay commands.

Preparation pins the already accepted platform; analysis is internal E2 truth
review, never an observed-only algorithm. No graph/frontend generation occurs.
"""
import argparse
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from .consumer_review import Archive
from .path_replay import row_digest
from .workflow import CONFIG, ROOT, cli, digest, host, read, save
from moe_ub_cosim.record_storage import iter_result

CONTROL = CONFIG/'controls/native-organization.json'


def controlled_network(original, mode, control):
    if mode not in (control['before'], control['after']):
        raise ValueError('Unknown queue control')
    if any(original['attributes'].get(k)!=v for k,v in control['fixed_attributes'].items()):
        raise ValueError('Fixed native capacities or splitting differ')
    if original['attributes'].get(control['attribute'],'fifo')!='fifo':
        raise ValueError('Original is not FIFO')
    changed=deepcopy(original)
    changed['attributes'][control['attribute']]=mode
    return changed


def prepare(archive, bundle, output, backend, mode):
    archive,bundle,output,backend=map(lambda p:Path(p).resolve(),(archive,bundle,output,backend))
    control=read(CONTROL)
    if digest(backend)!=control['backend_sha256']:
        raise ValueError('Backend differs from accepted queue platform')
    if subprocess.run(['git','diff','--quiet',control['platform_commit'],'--','python/moe_ub_cosim',
                       'backend','deps.lock.json','third_party/LLMServingSim','third_party/ns-3-ub'],cwd=ROOT).returncode:
        raise ValueError('Queue platform differs from accepted commit')
    original=Archive(archive)
    if original.index['archive_sha256']!=control['original_archive_sha256']:
        raise ValueError('Original archive differs from predeclared input')
    manifest=read(bundle/'slice.json')
    if manifest.get('compaction',{}).get('source_slice_sha256')!=original.index['entries']['slice/slice.json']['sha256']:
        raise ValueError('Compact slice is not bound to the original')
    names=['run/inputs/effective.json','run/inputs/observation.json','run/inputs/network/backend.json']
    records={n:json.loads(data) for n,data in original.members(names)}
    effective=records[names[0]];observation=records[names[1]];network=records[names[2]]
    if read(bundle/'inputs/network/backend.json')!=network:
        raise ValueError('Slice network differs from original run')
    replacement=controlled_network(network,mode,control)
    output.mkdir(parents=True,exist_ok=False)
    cli(output,'slice-verify','slice','inspect','--slice',bundle,'--verify')
    observation.update(schema_version='moe-ub-observation-config/5',native_queue=dict(
        schema_version='moe-ub-native-queue-config/2',end_ns=manifest['capture_end_ns'],**control['selection']))
    save(output/'observation.json',observation)
    shutil.copytree(bundle/'inputs/network',output/'network')
    save(output/'network/backend.json',replacement)
    config=dict(schema_version='moe-ub-replay/2',host=host(True,[output,bundle]),
        export_host=host(extra_roots=[output,bundle]),backend=str(backend),
        backend_timeout_seconds=effective['backend_timeout_seconds'],max_simulation_ns=effective['max_simulation_ns'],
        network=str(output/'network'),observation=str(output/'observation.json'),export_policy=str(CONFIG/'export-policy.json'))
    save(output/'replay.json',config)
    save(output/'selection.json',dict(schema_version='issue-screening-organization-selection/1',mode=mode,
        original_archive=str(archive),original_archive_sha256=original.index['archive_sha256'],
        original_manifest_sha256=original.index['entries']['run/manifest.json']['sha256'],
        slice=str(bundle),slice_sha256=digest(bundle/'slice.json'),control=control,control_sha256=digest(CONTROL),
        program_sha256=digest(__file__),backend_sha256=digest(backend),queue_policy=observation['native_queue']))
    save(output/'case.json',dict(schema_version='issue-screening-case/1',owned_by='scaleup_issue_screening',
        case_id='c1-organization-'+mode,platform_commit=control['platform_commit'],backend_sha256=digest(backend)))


def zipped_rows(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        for line in stream:
            if len(line)>4*2**20:raise ValueError('Oversized archived record')
            yield json.loads(line)


def resolved_members(archive,names):
    """Read selected hardlink aliases without extraction or dropping empty fields."""
    from .archive import safe_name
    links={};regular=set()
    with tarfile.open(archive.path,'r|gz') as stream:
        for member in stream:
            name=safe_name(member.name)
            if name in regular or name in links:raise ValueError('Duplicate archive member')
            if member.isfile():regular.add(name)
            elif member.islnk():
                target=safe_name(member.linkname)
                if target not in regular and target not in links:raise ValueError('Unresolved archive hardlink')
                links[name]=links.get(target,target)
                if archive.index['entries'][name]!=archive.index['entries'][links[name]]:
                    raise ValueError('Hardlink inventory identity mismatch')
            else:raise ValueError('Unsupported archive member type')
    targets={}
    for name in names:
        source=links.get(name,name)
        if source not in regular:raise ValueError('Missing regular archive content')
        targets.setdefault(source,[]).append(name)
    for source,data in archive.members(targets):
        for name in targets[source]:
            archive.used[name]=archive.index['entries'][name]
            yield name,data


def equivalent(archive, output):
    """Compare all old records and port fingerprints, without restoring old logs."""
    output=Path(output);old=Archive(archive);run=output/'run';execution=run/'raw/execution'
    cli(output,'validate-equivalence','validate','--run',run)
    selection=read(output/'selection.json')
    if selection['mode']!='fifo' or selection['original_archive_sha256']!=old.index['archive_sha256']:
        raise ValueError('FIFO equivalence has a different original')
    names=['run/raw/execution/result.json']
    names += [n for n in old.index['entries'] if
        (n.startswith('run/raw/execution/records/') and n.endswith('.jsonl.gz')) or
        (n.startswith('run/observed/records/') and n.endswith('.gz')) or
        (n.startswith('run/raw/execution/ports/') and n.endswith('/observation-audit.json'))]
    fields=('simulation_end_ns','service_finished_ns','random','network_drain','graph_completions',
            'rank_resources','boundary_resources','resource_lifecycle')
    report=read(execution/'result.json');records={};ports={}
    for name,data in resolved_members(old,names):
        target=output/name
        if name.endswith('/result.json'):
            before=json.loads(data)
            if any(before[k]!=report[k] for k in fields):raise ValueError('FIFO changed execution metadata')
        elif name.endswith('/observation-audit.json'):
            before=json.loads(data)
            if before!=read(target):raise ValueError('FIFO changed port wire/time fingerprint: '+name)
            ports[name]=before
        else:
            a=row_digest(zipped_rows(data))
            with gzip.open(target,'rt') as stream:b=row_digest(json.loads(line) for line in stream)
            if a!=b:raise ValueError('FIFO changed old record family: '+name)
            records[name]=a
    save(output/'port-equivalence.json',ports)
    value=dict(schema_version='issue-screening-organization-equivalence/1',passed=True,
        original_archive_sha256=old.index['archive_sha256'],original_manifest_sha256=selection['original_manifest_sha256'],
        replay_manifest_sha256=digest(run/'manifest.json'),selection_sha256=digest(output/'selection.json'),
        record_files=records,metadata_fields=fields,ports_compared=len(ports),
        packet_wire_and_start_fingerprints_match=True,port_equivalence_sha256=digest(output/'port-equivalence.json'),
        simulation_end_ns=report['simulation_end_ns'],backend_executed=True,frontend_executed=False,
        independent_statistical_repeat=False,program_sha256=digest(__file__))
    save(output/'equivalence.json',value)
    print(json.dumps(dict(passed=True,ports=len(ports),record_files=len(records))))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','equivalent'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--slice',type=Path)
    parser.add_argument('--backend',type=Path)
    parser.add_argument('--mode',choices=('fifo','producer_round_robin'))
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    a=parser.parse_args();a.output=a.output.resolve()
    if a.action=='run':
        selection=read(a.output/'selection.json')
        if digest(Path(selection['slice'])/'slice.json')!=selection['slice_sha256']:
            raise ValueError('Slice identity changed')
        cli(a.output,'replay','replay','--slice',selection['slice'],'--config',a.output/'replay.json','--output',a.output/'run')
        cli(a.output,'validate','validate','--run',a.output/'run')
    elif not a.worker:
        from moe_ub_cosim.host_probe import probe
        if not a.archive:parser.error('--archive is required')
        argv=[sys.executable,'-m',__spec__.name,a.action,'--output',str(a.output),'--archive',str(a.archive.resolve()),'--worker']
        if a.action=='prepare':
            if not all((a.slice,a.backend,a.mode)):parser.error('prepare needs --slice, --backend and --mode')
            argv += ['--slice',str(a.slice.resolve()),'--backend',str(a.backend.resolve()),'--mode',a.mode]
        result=probe(argv,str(a.output)+'-'+a.action+'-resources',**host(extra_roots=[a.output]))
        if result['status']!='completed':raise SystemExit(result['status'])
    elif a.action=='prepare':prepare(a.archive,a.slice,a.output,a.backend,a.mode)
    else:equivalent(a.archive,a.output)


if __name__=='__main__':main()
