"""Bounded offline checkpoint; never repeats a public replay or packet run."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import time

from .study import load,save,sha
from .run import budget
from moe_ub_cosim.host_probe import disk_usage

SCHEMA='hybrid-causal-checkpoint/1'


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
                raw=stream.extractfile(member).read()
                assert hashlib.sha256(raw).hexdigest()==entries[member.name]['sha256']
                path=output/member.name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    return manifest


def create(repo,run,archive):
    budget(run);assert load(run/'verification.json')['passed'] and not archive.exists()
    ledger=load(run/'execution.json');assert ledger['last_completed_phase']=='replays' and not ledger['closed']
    public=[r for r in ledger['records'] if 'hybrid-deepep-plan' in r['command'] or 'hybrid-deepep-replay' in r['command']]
    assert len(public)==56 and all(r['exit_code']==0 for r in ledger['records'])
    ledger.update(state='completed',closed=True,public_cli_commands=56,new_networks=0)
    save(run/'execution.json',ledger)
    paths={}
    for path in sorted(run.rglob('*')):
        if not path.is_file() or path.is_symlink():continue
        rel=path.relative_to(run)
        if rel.parts[0] in ('empty-cwd','recovered'):continue
        if rel.parts[0]=='monitor' and not (run/'monitor'/rel.parts[1]/'probe.json').exists():continue
        if rel.parts[0] not in ('observed','truth','analysis','report','logs','monitor') and len(rel.parts)!=1:continue
        if len(rel.parts)==1 and path.suffix!='.json':continue
        paths['run/'+str(rel)]=path
    for directory in ('experiments/hybrid_causality_audit','python/moe_ub_cosim'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.md') and 'results' not in path.relative_to(repo/directory).parts:
                paths['source/'+str(path.relative_to(repo))]=path
    manifest=dict(schema_version=SCHEMA,files={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in sorted(paths.items())})
    with tarfile.open(archive,'w:gz') as stream:
        for name,path in sorted(paths.items()):
            raw=path.read_bytes();info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644
            stream.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);info.mode=0o644;stream.addfile(info,io.BytesIO(raw))
    digest=sha(archive);assert admit(archive,digest)==manifest
    save(run/'checkpoint.json',dict(sha256=digest,bytes=archive.stat().st_size,members=len(paths)+1,
        all_member_hashes_checked=True,source_snapshot=True,new_networks=0))


def recover(run,archive,digest,output):
    budget(run)
    manifest=admit(archive,digest,output)
    source=output/'source';restored=output/'run';cwd=output/'empty-cwd';cwd.mkdir()
    env=os.environ|{'PYTHONPATH':str(source/'python')+':'+str(source),'PYTHONDONTWRITEBYTECODE':'1'}
    logs=[]
    def invoke(label,module,arguments):
        budget(run)
        command=[sys.executable,'-B','-m',module,*map(str,arguments)]
        before=time.monotonic();completed=subprocess.run(command,cwd=cwd,env=env,capture_output=True,timeout=55)
        row=dict(label=label,command=command,cwd=str(cwd),exit_code=completed.returncode,wall_seconds=time.monotonic()-before)
        logs.append(row);save(run/'recovery-execution.json',logs)
        (output/(label+'.stdout')).write_bytes(completed.stdout);(output/(label+'.stderr')).write_bytes(completed.stderr)
        assert completed.returncode==0,label
    # Reproduce transition hashes with copied source, into an independent result root.
    enumeration=output/'reenumeration';enumeration.mkdir()
    for domains in (2,3,4):
        invoke('exhaustive-'+str(domains),'experiments.hybrid_causality_audit.study',
               ['exhaustive','--run',enumeration,'--domains',domains])
        name='analysis/exhaustive-'+str(domains)+'.json'
        assert (enumeration/name).read_bytes()==(restored/name).read_bytes()
    invoke('predict','experiments.hybrid_causality_audit.study',
           ['predict','--run',restored,'--output',output/'predictions.json'])
    assert (output/'predictions.json').read_bytes()==(restored/'analysis/predictions.json').read_bytes()
    invoke('verify','experiments.hybrid_causality_audit.verify',
           ['--run',restored,'--output',output/'verification.json'])
    assert (output/'verification.json').read_bytes()==(restored/'verification.json').read_bytes()
    invoke('report','experiments.hybrid_causality_audit.report',['--run',restored,'--output',output/'report'])
    for name in ('report.md','report.html','metrics.json'):
        assert (output/'report'/name).read_bytes()==(restored/'report'/name).read_bytes()
    save(run/'recovery.json',dict(passed=True,archive_sha256=digest,members=len(manifest['files'])+1,
        all_member_hashes_checked=True,copied_source=True,new_cwd=True,
        repeated_public_replays=0,new_networks=0,recomputed_exhaustive_certificates=1527,
        byte_identical=['exhaustive-2.json','exhaustive-3.json','exhaustive-4.json','predictions.json','verification.json',
                        'report.md','report.html','metrics.json']))


def close(run):
    receipts=[load(p) for p in sorted((run/'monitor').glob('*/probe.json'))]
    assert all(p['status']=='completed' and p['child_exit_code']==0 and p['remaining_live_group_pids']==[] for p in receipts)
    # Conservative charge: outer receipt durations include every recovery subprocess.
    wall=sum(p['wall_seconds'] for p in receipts)
    allocation=disk_usage(run);assert wall+10<600 and max(allocation.values())+2*2**20<256*2**20
    assert load(run/'recovery.json')['passed'] and load(run/'execution.json')['closed']
    save(run/'closeout.json',dict(state='closed',all_completed_monitors=len(receipts),
        monitored_wall_seconds_before_closeout=wall,artifact_usage_before_closeout=allocation,
        reserved_admin_seconds=10,reserved_admin_bytes=2*2**20,wall_budget_seconds=600,artifact_budget_bytes=256*2**20,
        new_networks=0,public_cli_commands=56,repeated_public_replays=0,overall_goal='in_progress',paper_innovations_established=0))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('create','recover','close'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--archive',type=Path);p.add_argument('--sha256');p.add_argument('--output',type=Path);a=p.parse_args()
    run=a.run.resolve()
    if a.mode=='create':create(a.repository.resolve(),run,a.archive.resolve())
    elif a.mode=='recover':recover(run,a.archive.resolve(),a.sha256,a.output.resolve())
    else:close(run)
