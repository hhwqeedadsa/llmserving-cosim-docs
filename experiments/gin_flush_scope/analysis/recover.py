"""Bounded offline copied-source recovery, preserving terminal receipts."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(repository, stage):
    source_root=stage/'copied-source'
    assert not source_root.exists()
    for path in (repository/'experiments/gin_flush_scope').rglob('*.py'):
        target=source_root/path.relative_to(repository)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    shutil.copytree(Path(str(stage)+'-prepare-monitor'),stage/'prepare-monitor')
    for path in stage.rglob('probe.json'):
        row=json.loads(path.read_bytes())
        assert row['status'] in ('completed','child_failed') and not row['remaining_live_group_pids']
    files={str(p.relative_to(stage)):dict(sha256=sha(p),bytes=p.stat().st_size)
           for p in stage.rglob('*') if p.is_file()}
    assert len(files)<256 and sum(r['bytes'] for r in files.values())<8*2**20
    manifest=(json.dumps(files,sort_keys=True,indent=2)+'\n').encode()
    archive=stage/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        member=tarfile.TarInfo('manifest.json')
        member.size=len(manifest)
        tar.addfile(member,io.BytesIO(manifest))
        for name in sorted(files):
            tar.add(stage/name,arcname='evidence/'+name,recursive=False)
    restored=stage/'recovered'
    restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers()
        assert len(members)==len(files)+1==len({m.name for m in members})
        assert tar.extractfile('manifest.json').read()==manifest
        for member in members:
            assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
            if member.name=='manifest.json':
                continue
            assert member.name.startswith('evidence/')
            name=member.name[len('evidence/'):]
            assert name in files and member.size==files[name]['bytes']
            body=tar.extractfile(member).read()
            assert hashlib.sha256(body).hexdigest()==files[name]['sha256']
            target=restored/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(body)
    replay=restored/'recomputed'
    replay.mkdir()
    shutil.copytree(restored/'observed',replay/'observed')
    state=json.loads((restored/'state.json').read_bytes())
    state['status']='prepared'
    (replay/'state.json').write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    empty=restored/'empty-cwd'
    empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(restored/'copied-source'))
    base=[sys.executable,'-B','-m']
    commands=[base+['experiments.gin_flush_scope.analysis.study','execute','--stage',str(replay)],
              base+['experiments.gin_flush_scope.analysis.verify','--stage',str(replay),'--output',str(replay/'verification.json')],
              base+['experiments.gin_flush_scope.analysis.report','--stage',str(replay),'--output',str(replay/'report')]]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,env=env,capture_output=True,timeout=60)
        (stage/f'recovery-{i}.stdout').write_bytes(result.stdout)
        (stage/f'recovery-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr)
    for name in ('state.json','analysis.json','verification.json','report/report.md','report/report.html','report/key_metrics.json'):
        assert (stage/name).read_bytes()==(replay/name).read_bytes(),name
    result=dict(passed=True,archive_members=len(files)+1,archive_sha256=sha(archive),
        member_hashes_verified=True,copied_source_empty_cwd=True,analysis_verification_reports_byte_equal=True,
        commands=commands,HTTP=0,builds=0,network_runs=0,GPU=0)
    (stage/'recovery.json').write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository',type=Path,required=True)
    parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args()
    execute(args.repository.resolve(),args.stage.resolve())
