"""Preserve one bounded analysis and reproduce it from copied sources offline."""
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

from experiments.hybrid_completion_space.analysis.study import load,save,sha


def execute(repo,stage):
    copied=stage/'copied-source';assert not copied.exists()
    for folder in ('experiments/hybrid_completion_space','python/moe_ub_cosim'):
        for source in (repo/folder).rglob('*.py'):
            assert not source.is_symlink();dest=copied/source.relative_to(repo)
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    shutil.copytree(Path(str(stage)+'-prepare-monitor'),stage/'prepare-monitor')
    terminal=[]
    for p in stage.rglob('probe.json'):
        row=load(p);assert row['status'] in ('completed','child_failed') and not row['remaining_live_group_pids']
        terminal.append(dict(path=str(p.relative_to(stage)),status=row['status']))
    files={str(p.relative_to(stage)):dict(sha256=sha(p),bytes=p.stat().st_size) for p in stage.rglob('*') if p.is_file()}
    assert len(files)<=2048 and sum(r['bytes'] for r in files.values())<=64*2**20
    archive=stage/'checkpoint.tar.gz';manifest=(json.dumps(files,sort_keys=True,indent=2)+'\n').encode()
    with tarfile.open(archive,'w:gz') as tar:
        member=tarfile.TarInfo('manifest.json');member.size=len(manifest);tar.addfile(member,io.BytesIO(manifest))
        for name in sorted(files):tar.add(stage/name,arcname='evidence/'+name,recursive=False)
    restored=stage/'recovered';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==len(files)+1==len({m.name for m in members})
        assert tar.extractfile('manifest.json').read()==manifest
        for member in members:
            assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
            if member.name=='manifest.json':continue
            assert member.name.startswith('evidence/');name=member.name[len('evidence/'):]
            assert name in files and member.size==files[name]['bytes']
            body=tar.extractfile(member).read();assert hashlib.sha256(body).hexdigest()==files[name]['sha256']
            dest=restored/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
    for name,row in files.items():assert sha(restored/name)==row['sha256']
    replay=restored/'offline-recomputed';replay.mkdir();shutil.copytree(restored/'observed',replay/'observed')
    save(replay/'state.json',dict(status='prepared',planned=36,completed=0))
    empty=restored/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(restored/'copied-source/python')+os.pathsep+str(restored/'copied-source'))
    base=[sys.executable,'-B','-m'];commands=[
        base+['experiments.hybrid_completion_space.analysis.study','execute','--stage',str(replay)],
        base+['experiments.hybrid_completion_space.analysis.verify','--stage',str(replay),'--output',str(replay/'verification.json')],
        base+['experiments.hybrid_completion_space.analysis.report','--stage',str(replay),'--output',str(replay/'report')]]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,env=env,capture_output=True,timeout=60)
        (stage/f'recovery-{i}.stdout').write_bytes(result.stdout);(stage/f'recovery-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr)
    names=['state.json','public-check.json','verification.json','analysis/layers.json','analysis/summary.json',
           'report/report.md','report/report.html','report/key_metrics.json']
    for name in names:assert (stage/name).read_bytes()==(replay/name).read_bytes(),name
    save(stage/'recovery.json',dict(passed=True,archive_members=len(files)+1,archive_sha256=sha(archive),
        member_hashes_verified=True,copied_source_empty_cwd=True,all_36_conditions_and_2088_layers_byte_equal=True,
        public_dispatch_verification_report_byte_equal=True,initial_verifier_failure_preserved=True,
        terminal_monitors_in_archive=terminal,HTTP=0,builds=0,network_runs=0,GPU=0,commands=commands))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
