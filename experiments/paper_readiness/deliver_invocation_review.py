"""Package/check the bounded source-only invocation audit, including preserved failures."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def monitors(task,exclude):
    rows=[]
    for p in sorted(task.glob('*-monitor/probe.json')):
        if p.parent.name in exclude:continue
        r=load(p);assert not r['remaining_live_group_pids']
        if p.parent.name in ('input-monitor','check-monitor'):
            assert r['status']=='child_failed' and r['child_exit_code']==1
            message=(p.parent/'stderr.log').read_text()
            assert ('relative_to' in message if p.parent.name=='input-monitor' else 'excerpt' in message)
        else:assert r['status']=='completed' and r['child_exit_code']==0
        rows.append(dict(stage=p.parent.name,record=r))
    assert sum(r['record']['wall_seconds'] for r in rows)<240
    return rows


def package(a):
    task=a.task.resolve();report=a.report.resolve();out=task/'checkpoint';assert not out.exists()
    assert load(task/'checked-final/verification.json')['verified']
    assert load(report/'findings.json')['main_paper_contribution_established'] is False
    rows=monitors(task,{'package-monitor'})
    save(report/'maintenance.json',dict(monitors=rows,raw_volatile=True,persistent_backup=False,
        preserved_errors=[r['stage'] for r in rows if r['record']['child_exit_code']],
        changed_network_input_or_results=False,new_network_runs=0,source_download_repeated=False))
    entries={}
    def add(path,name):
        assert path.is_file() and not path.is_symlink() and name not in entries
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        entries[name]=dict(source=str(path),bytes=path.stat().st_size,sha256=sha(path))
    for p in sorted(task.rglob('*')):
        if p.is_file() and 'package-monitor' not in p.relative_to(task).parts:add(p,'raw/'+str(p.relative_to(task)))
    for p in sorted(report.iterdir()):
        if p.is_file():add(p,'report/'+p.name)
    files=[ROOT/'experiments/paper_readiness'/name for name in ('collect_invocation_sources.py','invocation_semantics.py',
        'check_invocation_semantics.py','build_invocation_review.py','deliver_invocation_review.py',
        'invocation_semantics_protocol.md','modern_backend_contract.md','README.md')]
    files += [ROOT/'python/moe_ub_cosim'/name for name in ('host_probe.py','host_processes.py','__init__.py')]
    files += [ROOT/'AGENTS.md',ROOT/'deps.lock.json',ROOT/'scripts/check_deps.sh',ROOT/'scripts/check_deps.py']
    for entry in load(task/'input-ready/observed/sources.json')['sources']:files.append(ROOT/entry['cache_path'])
    collection=a.collection.resolve()
    files += [p for p in collection.rglob('*') if p.is_file()]
    files += [ROOT/'data/research/deepep_modern_20261022/tree.json',ROOT/'data/research/nccl_gin_20261022/tree.json',
        ROOT/'data/research/nccl_gin_20261022/commit.json']
    for item in load(task/'input-ready/truth/manifest.json')['files']:files.append(ROOT/item['file']['path'])
    for p in sorted(set(files)):add(p,'source/'+str(p.relative_to(ROOT)))
    assert sum(e['bytes'] for e in entries.values())<=32*2**20
    out.mkdir();save(out/'members.json',entries);archive=out/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name,e in sorted(entries.items()):tar.add(e['source'],arcname=name,recursive=False)
        tar.add(out/'members.json',arcname='members.json',recursive=False)
    assert archive.stat().st_size<=16*2**20
    seen=set()
    with tarfile.open(archive,'r:gz') as tar:
        for m in tar:
            if m.name=='members.json':assert json.load(tar.extractfile(m))==entries;continue
            assert m.isfile() and m.name in entries and m.name not in seen
            raw=tar.extractfile(m).read();e=entries[m.name]
            assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256'];seen.add(m.name)
    assert seen==entries.keys()
    receipt=dict(path=str(archive),sha256=sha(archive),bytes=archive.stat().st_size,member_bytes=sum(e['bytes'] for e in entries.values()),
        verified_members=len(entries),raw_volatile=True,persistent_backup=False,new_network_runs=0,
        source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_worktree_has_research_edits=True,excludes='Active package monitor, archive itself, final check and later receipts',
        downloaded_source_executed=False,not_a_modern_runtime_image=True)
    save(out/'archive.json',receipt);save(report/'archive.json',receipt)
    print(json.dumps(dict(archive=True,**receipt)))


def check(a):
    assert not a.output.exists();task=a.task.resolve();report=a.report.resolve()
    for name,source in [('summary.json',task/'analysis/summary.json'),('verification.json',task/'checked-final/verification.json'),
        ('sources.json',task/'input-ready/observed/sources.json'),('protocol.md',ROOT/'experiments/paper_readiness/invocation_semantics_protocol.md')]:
        assert (report/name).read_bytes()==source.read_bytes()
    assert load(report/'verification.json')['verified'] and load(report/'verification.json')['source_task_assignments']==1080
    facts=load(report/'findings.json');assert facts['direct_automatic_requested_contexts']==17 and facts['api_default_hybrid']
    assert not facts['main_paper_contribution_established'] and not facts['real_invocation_gap_known']
    receipt=load(report/'archive.json');assert sha(Path(receipt['path']))==receipt['sha256']
    entries=load(task/'checkpoint/members.json');assert len(entries)==receipt['verified_members']
    source_count=syntax_count=0
    for name,e in entries.items():
        if not name.startswith('source/'):continue
        path=ROOT/name.removeprefix('source/');assert sha(path)==e['sha256'] and path.stat().st_size==e['bytes'];source_count+=1
        if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path));syntax_count+=1
    rows=monitors(task,{'final-monitor'})
    allocated=sum(p.stat().st_blocks*512 for p in task.rglob('*') if p.is_file() and not p.is_symlink())
    cache_bytes=sum(p.stat().st_blocks*512 for p in a.collection.rglob('*') if p.is_file())
    assert allocated+cache_bytes<=64*2**20
    assert 'PASS: checked 13 dependency checkouts' in (task/'deps-monitor/stdout.log').read_text()
    replay=load(task/'recovery/replay.json')
    assert replay['verified'] and replay['new_network_runs']==0 and replay['analysis_byte_identical']
    save(a.output,dict(verified=True,source_snapshots_matched=source_count,python_syntax_checked=syntax_count,
        archive_sha256=receipt['sha256'],archive_members_verified=receipt['verified_members'],
        monitored_seconds_before_final=sum(r['record']['wall_seconds'] for r in rows),
        allocated_bytes_before_final=allocated+cache_bytes,raw_volatile=True,persistent_backup=False,
        new_network_runs=0,downloaded_code_executed=False,offline_archive_replay_verified=True,
        main_paper_contribution_established=False))
    print(json.dumps(dict(verified=True,sources=source_count,syntax=syntax_count,allocated_bytes=allocated+cache_bytes)))


def replay(a):
    assert not a.output.exists();a.output.mkdir();task=a.task.resolve();out=a.output.resolve()
    receipt=load(a.report/'archive.json');archive=Path(receipt['path']);assert sha(archive)==receipt['sha256']
    members=load(task/'checkpoint/members.json');restored=[]
    with tarfile.open(archive,'r:gz') as tar:
        for m in tar:
            if not m.name.startswith('source/'):continue
            assert m.isfile() and '..' not in Path(m.name).parts and not Path(m.name).is_absolute()
            raw=tar.extractfile(m).read();e=members[m.name]
            assert len(raw)==e['bytes'] and hashlib.sha256(raw).hexdigest()==e['sha256']
            dest=out/m.name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);restored.append(m.name)
    fresh=out/'source';scripts=fresh/'experiments/paper_readiness';collection=fresh/a.collection.resolve().relative_to(ROOT)
    env=dict(os.environ,PYTHONPATH=str(fresh/'python'),PYTHONDONTWRITEBYTECODE='1');commands=[]
    stages=[('prepare',[sys.executable,str(scripts/'invocation_semantics.py'),'prepare','--sources',str(collection),
        '--manifest',str(task/'input-ready/truth/manifest.json'),'--output',str(out/'input-ready')]),
        ('analyze',[sys.executable,str(scripts/'invocation_semantics.py'),'analyze','--observed',str(out/'input-ready/observed'),
        '--output',str(out/'analysis')]),
        ('check',[sys.executable,str(scripts/'check_invocation_semantics.py'),'--input',str(out/'input-ready'),
        '--analysis',str(out/'analysis'),'--collection',str(collection),'--output',str(out/'checked')])]
    for name,command in stages:
        row=dict(stage=name,command=command,cwd=str(out));commands.append(row)
        try:
            with (out/(name+'.stdout.log')).open('w') as stdout,(out/(name+'.stderr.log')).open('w') as stderr:
                result=subprocess.run(command,cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=60)
            row['exit_code']=result.returncode;assert result.returncode==0
        finally:save(out/'commands.json',commands)
    original=task/'input-ready/observed';checked=[]
    assert {str(p.relative_to(original)) for p in original.rglob('*') if p.is_file()}=={str(p.relative_to(out/'input-ready/observed')) for p in (out/'input-ready/observed').rglob('*') if p.is_file()}
    for p in original.rglob('*'):
        if p.is_file():
            name=str(p.relative_to(original));assert p.read_bytes()==(out/'input-ready/observed'/name).read_bytes();checked.append(name)
    assert (task/'analysis/summary.json').read_bytes()==(out/'analysis/summary.json').read_bytes()
    assert (task/'checked-final/verification.json').read_bytes()==(out/'checked/verification.json').read_bytes()
    result=dict(verified=True,source_members_restored=len(restored),observed_files_byte_identical=checked,
        analysis_byte_identical=True,verification_byte_identical=True,cwd_changed=True,source_root_changed=True,
        new_network_runs=0,source_download_repeated=False,downloaded_code_executed=False)
    save(out/'replay.json',result);print(json.dumps(result))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='stage',required=True)
    for stage in ('package','check','replay'):
        q=sub.add_parser(stage)
        for k in ('task','report','collection'):q.add_argument('--'+k,type=Path,required=True)
        if stage in ('check','replay'):q.add_argument('--output',type=Path,required=True)
    a=p.parse_args();globals()[a.stage](a)


if __name__=='__main__':main()
