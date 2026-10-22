"""Archive the bounded source-capacity audit and its one new network condition."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('task','report','platform-checkpoint'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args();task=args.task.resolve();report=args.report.resolve();checkpoint=task/'checkpoint'
    assert not checkpoint.exists()
    verification=load(task/'checked-final/verification.json');execution=load(task/'run/execution.json')
    assert verification['verified'] and verification['all_declared_completed'] and verification['explanation_streams_checked']==36
    assert execution['new_network_runs']==1 and execution['reused_successful_runs']==2
    monitors=[]
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name=='package-monitor':continue
        row=load(path);assert row['status']=='completed' and row['child_exit_code']==0 and not row['remaining_live_group_pids']
        monitors.append(dict(stage=path.parent.name,record=row))
    assert sum(m['record']['wall_seconds'] for m in monitors)<240
    save(report/'maintenance.json',dict(monitors=monitors,
        monitored_seconds_before_packaging=sum(m['record']['wall_seconds'] for m in monitors),
        raw_volatile=True,persistent_backup=False,raw_path=str(task)))
    entries={}
    def add(path,name):
        assert path.is_file() and not path.is_symlink() and name not in entries
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        entries[name]=dict(source=str(path),bytes=path.stat().st_size,sha256=sha(path))
    for path in sorted(task.rglob('*')):
        if path.is_file() and 'package-monitor' not in path.relative_to(task).parts:add(path,'raw/'+str(path.relative_to(task)))
    for path in sorted(report.iterdir()):
        if path.is_file():add(path,'report/'+path.name)
    files=[*sorted((ROOT/'python/moe_ub_cosim').rglob('*.py')),*sorted((ROOT/'backend').rglob('*.cc')),
        *sorted((ROOT/'backend').rglob('*.hh')),ROOT/'backend/CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'AGENTS.md']
    files += [ROOT/'experiments/paper_readiness'/name for name in ('queue_underfill.py','queue_underfill_protocol.md',
        'check_queue_underfill.py','build_queue_underfill_review.py','package_queue_underfill.py','check_queue_underfill_delivery.py',
        'feedback_parameters.py','check_feedback_parameter_delivery.py','check_feedback_factorial.py',
        'prefill_pressure.py','analyze_dispatch_validation.py','README.md')]
    cached=set()
    for row in load(task/'input-corrected/truth/source-audit.json')['sources']:
        for key in ('path','manifest','tree'):cached.add(ROOT/row[key])
        if row['commit'].startswith('12df1a1'):cached.add((ROOT/row['tree']).parent/'commit.json')
    files += sorted(cached)
    for item in load(task/'input/truth/manifest.json')['files']:
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256'];files.append(path)
    for path in sorted(set(files)):add(path,'source/'+str(path.relative_to(ROOT)))
    prior=args.platform_checkpoint.resolve();old_archive=prior/'checkpoint.tar.gz';old_members=load(prior/'members.json')
    assert sha(old_archive)=='4932e8bdb9597821b3e5437f43120eb10f4b29e6efe8ee437c48fac9f4c09cfd'
    verified_old=set()
    with tarfile.open(old_archive,'r:gz') as tar:
        for member in tar:
            if member.name=='members.json':continue
            data=tar.extractfile(member).read();entry=old_members[member.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];verified_old.add(member.name)
    assert verified_old==old_members.keys() and len(verified_old)==648
    add(old_archive,'prior-platform/checkpoint.tar.gz');add(prior/'members.json','prior-platform/members.json')
    binary=Path(execution['backend_path']);assert sha(binary)==execution['backend_sha256'];add(binary,'runtime/bin/'+binary.name)
    libraries=[]
    for name in load(task/'run/d1024-ai40-p001/result.json')['loaded_libraries']:
        path=Path(name)
        if not path.is_absolute():continue
        actual=path.resolve()
        if path.name.startswith('libns3.'):
            member='raw/runtime/'+path.name;assert sha(actual)==old_members[member]['sha256']
            arc='prior-platform/checkpoint.tar.gz::'+member
        else:
            arc='runtime/lib/'+path.name;add(actual,arc)
        libraries.append(dict(original_path=name,archive_path=arc,sha256=sha(actual)))
    member_bytes=sum(e['bytes'] for e in entries.values());assert member_bytes<=96*2**20
    checkpoint.mkdir();save(checkpoint/'members.json',entries);archive=checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',dereference=True) as tar:
        for name,entry in sorted(entries.items()):tar.add(entry['source'],arcname=name,recursive=False)
        tar.add(checkpoint/'members.json',arcname='members.json',recursive=False)
    assert archive.stat().st_size<=48*2**20
    verified=set()
    with tarfile.open(archive,'r:gz') as tar:
        for member in tar:
            if member.name=='members.json':assert json.load(tar.extractfile(member))==entries;continue
            assert member.name not in verified;data=tar.extractfile(member).read();entry=entries[member.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];verified.add(member.name)
    assert verified==entries.keys()
    receipt=dict(path=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,member_bytes=member_bytes,
        verified_members=len(verified),nested_platform_members_verified=648,runtime=libraries,binary_sha256=sha(binary),
        source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),source_worktree_has_research_edits=True,
        volatile=True,persistent_backup=False,runtime_portability='Original ELF RPATH remains; not a portable runtime image',
        new_network_runs=1,reused_successful_runs=2,excludes='Active package monitor, archive itself and post-package receipts/final checks',
        static_direction_correction_preserved=True,network_inputs_unchanged_by_correction=True)
    save(checkpoint/'archive.json',receipt);save(report/'archive.json',receipt)
    print(json.dumps(dict(archive=True,members=len(verified),bytes=receipt['bytes'],sha256=receipt['sha256'])))


if __name__=='__main__':main()
