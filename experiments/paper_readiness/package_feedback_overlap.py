"""Package this completed overlap study and minimal prior-window comparison inputs."""
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
    for k in ('task','report','platform-checkpoint','short-study','papers'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();task=a.task.resolve();report=a.report.resolve();checkpoint=task/'checkpoint'
    assert not checkpoint.exists()
    monitors=[]
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name=='package-monitor':continue
        r=load(path);assert r['status']=='completed' and not r['remaining_live_group_pids']
        monitors.append(dict(stage=path.parent.name,record=r))
    assert sum(m['record']['wall_seconds'] for m in monitors)<240
    execution=load(task/'run/execution.json');assert execution['completed'] and execution['new_network_runs']==4
    assert load(task/'checked-refined/verification.json')['verified']
    assert load(task/'comparison/comparison.json')['verified_matching_network_and_controller']
    binary=Path(execution['backend_path']);assert sha(binary)==execution['backend_sha256']
    prior=a.platform_checkpoint.resolve();old_archive=prior/'checkpoint.tar.gz';old_members=load(prior/'members.json')
    assert sha(old_archive)=='4932e8bdb9597821b3e5437f43120eb10f4b29e6efe8ee437c48fac9f4c09cfd'
    verified_old=0
    with tarfile.open(old_archive,'r:gz') as old:
        for member in old:
            if member.name not in old_members:continue
            data=old.extractfile(member).read();entry=old_members[member.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];verified_old+=1
    assert verified_old==len(old_members)==648
    entries={}
    def add(path,name):
        assert name not in entries and path.is_file() and not path.is_symlink()
        entries[name]=dict(source=str(path),bytes=path.stat().st_size,sha256=sha(path))
    for path in sorted(task.rglob('*')):
        if path.is_file() and 'package-monitor' not in path.relative_to(task).parts:add(path,'raw/'+str(path.relative_to(task)))
    save(report/'maintenance.json',dict(monitors=monitors,
        monitored_seconds_before_packaging=sum(m['record']['wall_seconds'] for m in monitors),
        raw_path=str(task),raw_volatile=True,persistent_backup=False))
    for path in sorted(report.iterdir()):
        if path.is_file():add(path,'report/'+path.name)
    files=[*sorted((ROOT/'python/moe_ub_cosim').rglob('*.py')),*sorted((ROOT/'backend').rglob('*.cc')),
        *sorted((ROOT/'backend').rglob('*.hh')),ROOT/'backend/CMakeLists.txt',ROOT/'deps.lock.json',ROOT/'AGENTS.md',
        *[ROOT/'experiments/paper_readiness'/name for name in ('feedback_overlap.py','check_feedback_overlap.py',
          'compare_feedback_windows.py','build_feedback_overlap_review.py','package_feedback_overlap.py',
          'feedback_overlap_protocol.md','check_feedback_factorial.py','prefill_pressure.py',
          'analyze_dispatch_validation.py','README.md')],
        ROOT/'examples/scaleout_rdma/small-clos-100g.json',ROOT/'examples/scaleout_rdma/rdma.json']
    for path in files:
        if path.is_file():add(path,'source/'+str(path.relative_to(ROOT)))
    manifest=load(task/'input/truth/manifest.json')
    for item in manifest['files']:
        path=ROOT/item['file']['path'];assert sha(path)==item['file']['sha256'];add(path,'source/'+item['file']['path'])
    short=a.short_study.resolve()
    short_files=['input/observed/topology.json','input/observed/protocol.json',
        'input/truth/manifest.json','input/truth/provenance.json','run/execution.json',
        'analysis/summary.json','checked-final/verification.json']
    for depth in (1,8):
        name=f'd{depth}-n50000-r55000-b48'
        short_files.extend(['input/observed/'+name+'.json','run/'+name+'/result.json'])
    for name in short_files:add(short/name,'prior-short/'+name)
    add(old_archive,'prior-platform/checkpoint.tar.gz');add(prior/'members.json','prior-platform/members.json')
    add(binary,'runtime/bin/'+binary.name)
    libraries=[]
    for name in load(task/'run/d8-r55000/result.json')['loaded_libraries']:
        path=Path(name)
        if not path.is_absolute():continue
        actual=path.resolve()
        if path.name.startswith('libns3.'):
            member='raw/runtime/'+path.name;assert sha(actual)==old_members[member]['sha256']
            arc='prior-platform/checkpoint.tar.gz::'+member
        else:
            arc='runtime/lib/'+path.name;add(actual,arc)
        libraries.append(dict(original_path=name,archive_path=arc,sha256=sha(actual)))
    # Keep the primary paper used for the parameter/novelty caveat, without fetching.
    papers=a.papers.resolve()
    paper=papers/'dcqcn2015.pdf'
    assert sha(paper)=='879074a33b78ceb93f9f66d59b217fba9e7621320cc68bc47914747cf5cf31f8'
    for name in ('dcqcn2015.pdf','dcqcn2015.txt'):add(papers/name,'papers/'+name)
    assert sum(e['bytes'] for e in entries.values())<64*2**20
    checkpoint.mkdir();save(checkpoint/'members.json',entries)
    archive=checkpoint/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',dereference=True) as tar:
        for name,entry in sorted(entries.items()):tar.add(entry['source'],arcname=name,recursive=False)
        tar.add(checkpoint/'members.json',arcname='members.json',recursive=False)
    assert archive.stat().st_size<=32*2**20
    verified=0
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            if item.name=='members.json':continue
            data=tar.extractfile(item).read();entry=entries[item.name]
            assert len(data)==entry['bytes'] and hashlib.sha256(data).hexdigest()==entry['sha256'];verified+=1
    assert verified==len(entries)
    receipt=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),verified_members=verified,
        member_bytes=sum(e['bytes'] for e in entries.values()),nested_platform_members_verified=verified_old,
        runtime=libraries,binary_sha256=sha(binary),source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_worktree_has_research_edits=True,volatile=True,persistent_backup=False,
        runtime_portability='Original ELF RPATH remains; not a portable runtime image',
        prior_short_scope='Minimal two-condition comparison inputs/results, not all 16 historical cases',
        excluded='Active package monitor and archive itself; no additional network execution')
    save(report/'archive.json',receipt);save(checkpoint/'archive.json',receipt)
    print(json.dumps(dict(archive=True,verified_members=verified,bytes=receipt['bytes'],sha256=receipt['sha256'])))


if __name__=='__main__':main()
