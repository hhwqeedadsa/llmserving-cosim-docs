"""Archive this fixed completed study, including runtime bytes; never execute a network."""
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
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('task','report','platform-checkpoint'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();task=a.task.resolve();report=a.report.resolve()
    checkpoint=task/'checkpoint';assert not checkpoint.exists()
    monitors=[]
    for directory in sorted(task.glob('*-monitor')):
        if directory.name=='package-monitor':continue
        record=load(directory/'probe.json')
        if directory.name=='failed-package-monitor':
            assert record['status']=='child_failed' and not record['remaining_live_group_pids']
            assert "sum(v['bytes'] for v in members.values())<64*2**20" in (directory/'stderr.log').read_text()
        else:assert record['status']=='completed' and not record['remaining_live_group_pids']
        monitors.append(dict(stage=directory.name,record=record))
    assert sum(m['record']['wall_seconds'] for m in monitors)<240
    assert load(task/'checked-final/verification.json')['verified']
    execution=load(task/'run/execution.json')
    assert execution['completed'] and execution['conditions']==16
    binary=Path(execution['backend_path'])
    assert sha(binary)==execution['backend_sha256']
    members={}
    def add(path,name):
        assert name not in members and path.is_file()
        members[name]=dict(source=str(path),bytes=path.stat().st_size,sha256=sha(path))
    for path in sorted(task.rglob('*')):
        if path.is_file() and 'package-monitor' not in path.relative_to(task).parts:
            assert not path.is_symlink()
            add(path,'raw/'+str(path.relative_to(task)))
    for path in sorted(report.iterdir()):
        if path.is_file():add(path,'report/'+path.name)
    # Store experiment/public platform source, without third-party generated trees or tests.
    source_paths=[*sorted((ROOT/'python/moe_ub_cosim').rglob('*.py')),
        *sorted((ROOT/'backend').rglob('*.cc')),*sorted((ROOT/'backend').rglob('*.hh')),
        ROOT/'backend/CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'AGENTS.md',
        *[ROOT/'experiments/paper_readiness'/name for name in (
            'feedback_factorial.py','check_feedback_factorial.py','build_feedback_review.py',
            'package_feedback_factorial.py','feedback_factorial_protocol.md','prefill_pressure.py',
            'analyze_dispatch_validation.py','README.md')],
        ROOT/'examples/scaleout_rdma/small-clos-100g.json',ROOT/'examples/scaleout_rdma/rdma.json']
    for path in source_paths:
        if path.is_file():add(path,'source/'+str(path.relative_to(ROOT)))
    manifest=load(task/'input/truth/manifest.json')
    for entry in manifest['files']:
        path=ROOT/entry['file']['path'];assert sha(path)==entry['file']['sha256']
        add(path,'source/'+entry['file']['path'])
    add(binary,'runtime/bin/'+binary.name)
    result=load(task/'run/d8-n5000-r5000-b48/result.json')
    runtime=[]
    prior=a.platform_checkpoint.resolve()
    prior_archive=prior/'checkpoint.tar.gz'
    assert sha(prior_archive)=='4932e8bdb9597821b3e5437f43120eb10f4b29e6efe8ee437c48fac9f4c09cfd'
    prior_members=load(prior/'members.json')
    prior_verified=0
    with tarfile.open(prior_archive,'r:gz') as old:
        for item in old:
            if item.name not in prior_members:continue
            entry=prior_members[item.name]
            data=old.extractfile(item).read()
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256']
            prior_verified+=1
    assert prior_verified==len(prior_members)==648
    add(prior_archive,'prior-platform/checkpoint.tar.gz')
    add(prior/'members.json','prior-platform/members.json')
    for name in result['loaded_libraries']:
        path=Path(name)
        if not path.is_absolute():continue
        actual=path.resolve()
        if path.name.startswith('libns3.'):
            old_name='raw/runtime/'+path.name
            assert sha(actual)==prior_members[old_name]['sha256']
            archive_path='prior-platform/checkpoint.tar.gz::'+old_name
        else:
            archive_path='runtime/lib/'+path.name
            add(actual,archive_path)
        runtime.append(dict(original_path=name,archive_path=archive_path,sha256=sha(actual)))
    assert sum(v['bytes'] for v in members.values())<64*2**20
    checkpoint.mkdir()
    save(report/'maintenance.json',dict(monitors=monitors,
        monitored_seconds_before_packaging=sum(m['record']['wall_seconds'] for m in monitors),
        raw_path=str(task),raw_volatile=True,persistent_backup=False))
    add(report/'maintenance.json','report/maintenance.json')
    save(checkpoint/'members.json',members)
    archive=checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',dereference=True) as tar:
        for name,identity in sorted(members.items()):tar.add(identity['source'],arcname=name,recursive=False)
        tar.add(checkpoint/'members.json',arcname='members.json',recursive=False)
    assert archive.stat().st_size<=32*2**20
    verified=0
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            if item.name=='members.json':continue
            identity=members[item.name]
            data=tar.extractfile(item).read()
            assert len(data)==identity['bytes'] and hashlib.sha256(data).hexdigest()==identity['sha256']
            verified+=1
    assert verified==len(members)
    receipt=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),
        verified_members=verified,member_bytes=sum(v['bytes'] for v in members.values()),
        nested_platform_members_verified=prior_verified,
        runtime=runtime,binary_sha256=sha(binary),source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_worktree_has_research_edits=True,volatile=True,persistent_backup=False,
        runtime_portability='ELF keeps original RPATH; archived library bytes are not a portable runtime image',
        preserved_failure='First pack exceeded 64MiB planned members before archive creation; preserved original code/log. Same cap, reuse hash-verified prior platform archive instead of expanding its native libraries. No network retry.',
        excluded='active package monitor, checkpoint archive itself; no second network run')
    save(report/'archive.json',receipt)
    save(checkpoint/'archive.json',receipt)
    print(json.dumps(dict(archive=True,bytes=receipt['bytes'],verified_members=verified,sha256=receipt['sha256'])))


if __name__=='__main__':main()
