"""Bounded cross-window evidence/source/runtime archive, with member verification."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for k in ('task','report','platform-checkpoint'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();task=a.task.resolve();report=a.report.resolve();checkpoint=task/'checkpoint'
    assert not checkpoint.exists()
    assert load(task/'checked-chain/verification.json')['all_declared_completed']
    execution=load(task/'run/execution.json');assert execution['completed'] and execution['new_network_runs']==3
    monitors=[]
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name=='package-monitor':continue
        r=load(path)
        assert not r['remaining_live_group_pids']
        if path.parent.name=='check-final-monitor':
            assert r['status']=='child_failed' and r['child_exit_code']==1
            assert 'ValueError: min() iterable argument is empty' in (path.parent/'stderr.log').read_text()
        else:assert r['status']=='completed' and r['child_exit_code']==0
        monitors.append(dict(stage=path.parent.name,record=r))
    assert sum(m['record']['wall_seconds'] for m in monitors)<240
    save(report/'maintenance.json',dict(monitors=monitors,
        monitored_seconds_before_packaging=sum(m['record']['wall_seconds'] for m in monitors),
        checker_failure_preserved='check-final-monitor; empty CE list in critical-connection enrichment',
        correction='Exclude empty mark lists from first-CE min(); no network change or rerun',
        raw_volatile=True,persistent_backup=False,raw_path=str(task)))
    entries={}
    def add(path,name):
        assert path.is_file() and not path.is_symlink() and name not in entries
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        entries[name]=dict(source=str(path),bytes=path.stat().st_size,sha256=sha(path))
    for path in sorted(task.rglob('*')):
        if path.is_file() and 'package-monitor' not in path.relative_to(task).parts:
            add(path,'raw/'+str(path.relative_to(task)))
    for path in sorted(report.iterdir()):
        if path.is_file():add(path,'report/'+path.name)
    files=[*sorted((ROOT/'python/moe_ub_cosim').rglob('*.py')),
        *sorted((ROOT/'backend').rglob('*.cc')),*sorted((ROOT/'backend').rglob('*.hh')),
        ROOT/'backend/CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'AGENTS.md',ROOT/'scripts/check_deps.sh']
    files += [ROOT/'experiments/paper_readiness'/n for n in ('cross_window.py','cross_window_protocol.md',
        'check_cross_window.py','build_cross_window_review.py','package_cross_window.py','check_cross_window_delivery.py',
        'feedback_parameters.py','prefill_pressure.py','check_feedback_factorial.py','analyze_dispatch_validation.py','README.md')]
    for item in load(task/'input/truth/manifest.json')['files']:
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256'];files.append(path)
    for path in sorted(set(files)):add(path,'source/'+str(path.relative_to(ROOT)))
    prior=a.platform_checkpoint.resolve();old=prior/'checkpoint.tar.gz';old_members=load(prior/'members.json')
    assert sha(old)=='4932e8bdb9597821b3e5437f43120eb10f4b29e6efe8ee437c48fac9f4c09cfd'
    seen=set()
    with tarfile.open(old,'r:gz') as tar:
        for m in tar:
            if m.name=='members.json':continue
            data=tar.extractfile(m).read();e=old_members[m.name]
            assert len(data)==e['bytes'] and hashlib.sha256(data).hexdigest()==e['sha256'];seen.add(m.name)
    assert seen==old_members.keys() and len(seen)==648
    add(old,'prior-platform/checkpoint.tar.gz');add(prior/'members.json','prior-platform/members.json')
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
        for name,e in sorted(entries.items()):tar.add(e['source'],arcname=name,recursive=False)
        tar.add(checkpoint/'members.json',arcname='members.json',recursive=False)
    assert archive.stat().st_size<=48*2**20
    seen=set()
    with tarfile.open(archive,'r:gz') as tar:
        for m in tar:
            if m.name=='members.json':assert json.load(tar.extractfile(m))==entries;continue
            data=tar.extractfile(m).read();e=entries[m.name]
            assert m.name not in seen and len(data)==e['bytes'] and hashlib.sha256(data).hexdigest()==e['sha256'];seen.add(m.name)
    assert seen==entries.keys()
    receipt=dict(path=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,member_bytes=member_bytes,
        verified_members=len(seen),nested_platform_members_verified=648,runtime=libraries,
        binary_sha256=sha(binary),source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_worktree_has_research_edits=True,volatile=True,persistent_backup=False,
        runtime_portability='Original ELF RPATH remains; not a portable runtime image',
        new_network_runs=3,reused_network_runs=0,cold_references_read_only=3,
        excludes='Active package monitor, archive itself and post-package receipts/final checks',
        preserved_checker_failure=True)
    save(checkpoint/'archive.json',receipt);save(report/'archive.json',receipt)
    print(json.dumps(dict(archive=True,members=len(seen),bytes=receipt['bytes'],sha256=receipt['sha256'])))


if __name__=='__main__':main()
