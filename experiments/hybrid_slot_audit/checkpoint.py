"""Hashed archive and copied-source offline recomputation; no public-plan rerun."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile
import time
from .common import load,save,sha
from .run import budget
from moe_ub_cosim.host_probe import disk_usage

SCHEMA='hybrid-slot-checkpoint/1'


def admit(archive,digest,output=None):
    assert archive.stat().st_size<64*2**20 and sha(archive)==digest
    entries={};total=0;manifest=None
    with tarfile.open(archive,'r:gz') as stream:
        for member in stream:
            path=PurePosixPath(member.name)
            assert member.isfile() and path.parts and not path.is_absolute() and '..' not in path.parts
            assert str(path)==member.name and '\\' not in member.name and member.name not in entries
            assert len(entries)<4096 and 0<=member.size<=16*2**20 and member.mode==0o644
            total+=member.size;assert total<128*2**20
            raw=stream.extractfile(member).read();assert len(raw)==member.size
            entries[member.name]=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            if member.name=='manifest.json':manifest=json.loads(raw)
    assert manifest is not None and manifest['schema_version']==SCHEMA
    del entries['manifest.json'];assert entries==manifest['files']
    assert all(not any(str(p) in entries for p in PurePosixPath(n).parents) for n in entries)
    if output is not None:
        output.mkdir()
        with tarfile.open(archive,'r:gz') as stream:
            for member in stream:
                if member.name=='manifest.json':continue
                raw=stream.extractfile(member).read();assert hashlib.sha256(raw).hexdigest()==entries[member.name]['sha256']
                p=output/member.name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    return manifest


def create(repo,run,archive):
    budget(run);assert load(run/'verification.json')['passed'] and not archive.exists()
    ledger=load(run/'execution.json');assert not ledger['closed'] and ledger['last_completed_phase']=='interpret'
    assert len([r for r in ledger['records'] if 'hybrid-deepep-plan' in r['command']])==8
    assert all(r['exit_code']==0 for r in ledger['records'])
    ledger.update(state='completed',closed=True,public_plans=8,new_networks=0);save(run/'execution.json',ledger)
    paths={}
    for p in sorted(run.rglob('*')):
        if not p.is_file() or p.is_symlink():continue
        rel=p.relative_to(run)
        if rel.parts[0]=='monitor' and not (run/'monitor'/rel.parts[1]/'probe.json').exists():continue
        if rel.parts[0] not in ('observed','truth','analysis','report','logs','monitor') and len(rel.parts)!=1:continue
        if len(rel.parts)==1 and p.suffix!='.json':continue
        paths['run/'+str(rel)]=p
    for directory in ('experiments/hybrid_slot_audit','python/moe_ub_cosim'):
        for p in sorted((repo/directory).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.md') and 'results' not in p.relative_to(repo/directory).parts:
                paths['source/'+str(p.relative_to(repo))]=p
    manifest=dict(schema_version=SCHEMA,files={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(paths.items())})
    with tarfile.open(archive,'w:gz') as stream:
        for name,p in sorted(paths.items()):
            raw=p.read_bytes();info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;stream.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json')
        info.size=len(raw);info.mode=0o644;stream.addfile(info,io.BytesIO(raw))
    digest=sha(archive);assert admit(archive,digest)==manifest
    save(run/'checkpoint.json',dict(sha256=digest,bytes=archive.stat().st_size,members=len(paths)+1,all_member_hashes_checked=True))


def recover(run,archive,digest,output):
    budget(run);manifest=admit(archive,digest,output)
    restored=output/'run';source=output/'source';cwd=output/'empty-cwd';cwd.mkdir()
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'};ledger=[]
    def command(label,module,arguments):
        budget(run);argv=[sys.executable,'-B','-m',module,*map(str,arguments)];before=time.monotonic()
        completed=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,timeout=55)
        ledger.append(dict(label=label,command=argv,cwd=str(cwd),exit_code=completed.returncode,wall_seconds=time.monotonic()-before))
        save(run/'recovery-execution.json',ledger)
        (output/(label+'.stdout')).write_bytes(completed.stdout);(output/(label+'.stderr')).write_bytes(completed.stderr)
        assert completed.returncode==0,label
    command('reference','experiments.hybrid_slot_audit.study',['reference','--run',restored,'--output',output/'reference.json'])
    assert (output/'reference.json').read_bytes()==(restored/'analysis/reference.json').read_bytes()
    command('interpret','experiments.hybrid_slot_audit.study',['interpret','--run',restored,'--output',output/'recomputed-truth'])
    original={str(p.relative_to(restored/'truth')):sha(p) for p in (restored/'truth').rglob('*.json')}
    recomputed={str(p.relative_to(output/'recomputed-truth')):sha(p) for p in (output/'recomputed-truth').rglob('*.json')}
    assert original==recomputed and len(original)==157
    command('verify','experiments.hybrid_slot_audit.verify',['--run',restored,'--output',output/'verification.json'])
    assert (output/'verification.json').read_bytes()==(restored/'verification.json').read_bytes()
    command('report','experiments.hybrid_slot_audit.report',['--run',restored,'--output',output/'report'])
    for name in ('report.md','report.html','metrics.json'):assert (output/'report'/name).read_bytes()==(restored/'report'/name).read_bytes()
    save(run/'recovery.json',dict(passed=True,archive_sha256=digest,members=len(manifest['files'])+1,
        all_member_hashes_checked=True,copied_source=True,new_cwd=True,repeated_public_plans=0,new_networks=0,
        recomputed_declared_traces=156,byte_identical=['reference.json','157 truth/index files','verification.json','report.md','report.html','metrics.json']))


def close(run):
    receipts=[load(p) for p in sorted((run/'monitor').glob('*/probe.json'))]
    assert all(r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids'] for r in receipts)
    wall=sum(r['wall_seconds'] for r in receipts);size=disk_usage(run)
    assert wall+10<600 and max(size.values())+2*2**20<256*2**20
    assert load(run/'execution.json')['closed'] and load(run/'recovery.json')['passed']
    save(run/'closeout.json',dict(state='closed',completed_monitors_before_closeout=len(receipts),monitored_wall_seconds_before_closeout=wall,
        artifact_usage_before_closeout=size,reserved_admin_seconds=10,reserved_admin_bytes=2*2**20,
        wall_budget_seconds=600,artifact_budget_bytes=256*2**20,public_plans=8,new_networks=0,
        overall_goal='in_progress',paper_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('create','recover','close'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True);p.add_argument('--archive',type=Path)
    p.add_argument('--sha256');p.add_argument('--output',type=Path);a=p.parse_args();run=a.run.resolve()
    if a.mode=='create':create(a.repository.resolve(),run,a.archive.resolve())
    elif a.mode=='recover':recover(run,a.archive.resolve(),a.sha256,a.output.resolve())
    else:close(run)
