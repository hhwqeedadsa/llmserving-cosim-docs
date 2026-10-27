"""Bounded hashed recovery of route/source accounting; never executes a network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from .study import load,save,sha

SCHEMA='hybrid-path-audit-checkpoint/1'


def admit(archive,digest,output=None):
    assert archive.stat().st_size<32*2**20 and sha(archive)==digest
    entries={};total=0;manifest=None
    with tarfile.open(archive,'r:gz') as stream:
        for member in stream:
            path=PurePosixPath(member.name)
            assert member.isfile() and path.parts and not path.is_absolute() and '..' not in path.parts
            assert str(path)==member.name and '\\' not in member.name and member.name not in entries
            assert len(entries)<2048 and 0<=member.size<=16*2**20 and member.mode==0o644
            total+=member.size;assert total<96*2**20
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
    assert load(run/'verification.json')['passed'] and not archive.exists()
    assert load(run/'offline-continuation.json')['closed'] and load(run/'offline-continuation.json')['state']=='completed'
    original=load(run/'execution.json')
    assert original['closed'] and original['state']=='failure_preserved'
    paths={}
    for path in sorted(run.rglob('*')):
        if not path.is_file() or path.is_symlink():continue
        rel=path.relative_to(run)
        if rel.parts[0] not in ('observed','report','monitor','failed-source') and len(rel.parts)!=1:continue
        if len(rel.parts)==1 and path.suffix!='.json':continue
        if rel.parts[0]=='monitor' and not (run/'monitor'/rel.parts[1]/'probe.json').exists():continue
        paths['run/'+str(rel)]=path
    for path in (repo/'experiments/hybrid_path_audit').glob('*'):
        if path.suffix in ('.py','.md'):paths['source/'+str(path.relative_to(repo))]=path
    entries={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in sorted(paths.items())}
    assert len(entries)<2047 and sum(r['bytes'] for r in entries.values())<96*2**20
    with tarfile.open(archive,'x:gz',compresslevel=6) as stream:
        for name,path in sorted(paths.items()):
            raw=path.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size,info.mode=len(raw),0o644
            stream.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(dict(schema_version=SCHEMA,files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size,info.mode=len(raw),0o644
        stream.addfile(info,io.BytesIO(raw))
    digest=sha(archive);admit(archive,digest)
    save(archive.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        uncompressed_bytes=sum(r['bytes'] for r in entries.values()),compressed_bytes=archive.stat().st_size))


def restore(archive,digest,output):
    assert not output.exists();manifest=admit(archive,digest,output)
    run,source=output/'run',output/'source';empty=output/'empty-cwd';empty.mkdir()
    env=os.environ|{'PYTHONPATH':str(source),'PYTHONDONTWRITEBYTECODE':'1'};records=[]
    for module,args in (
        ('study',('analyze','--run',run,'--output',run/'recomputed-analysis.json')),
        ('verify',('--run',run,'--output',run/'recomputed-verification.json')),
        ('report',('--run',run,'--output',run/'recomputed-report')),
    ):
        argv=[sys.executable,'-B','-m','experiments.hybrid_path_audit.'+module,*map(str,args)]
        result=subprocess.run(argv,cwd=empty,env=env,capture_output=True,text=True,timeout=50)
        records.append(dict(command=argv,cwd=str(empty),exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
        save(output/'commands.json',records);assert result.returncode==0,records[-1]
    assert (run/'analysis.json').read_bytes()==(run/'recomputed-analysis.json').read_bytes()
    assert (run/'verification.json').read_bytes()==(run/'recomputed-verification.json').read_bytes()
    for name in ('report.md','report.html','key_metrics.json'):
        assert (run/'report'/name).read_bytes()==(run/'recomputed-report'/name).read_bytes()
    save(output/'recovery.json',dict(passed=True,archive_sha256=digest,members=len(manifest['files'])+1,
        copied_source=True,empty_cwd=True,analysis_identical=True,verification_identical=True,
        reports_identical=True,original_failure_preserved=True,new_networks=0,new_GPU_runs=0,new_builds=0))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('create','restore'))
    p.add_argument('--repository',type=Path);p.add_argument('--run',type=Path)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256');p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.run.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.sha256,a.output.resolve())
