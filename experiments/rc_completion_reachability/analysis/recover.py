"""Hash archive and offline copied-source recovery; no builds or HTTP."""
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

from experiments.rc_completion_reachability.analysis.sources import load, save, sha


def execute(repo,stage):
    copied=stage/'copied-source';copied.mkdir()
    for p in (repo/'experiments/rc_completion_reachability').rglob('*.py'):
        name=p.relative_to(repo);dest=copied/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
    intake_monitor=Path(str(stage)+'-fetch0-monitor');shutil.copytree(intake_monitor,stage/'intake-monitor')
    terminal=[]
    for p in stage.rglob('probe.json'):
        row=load(p);assert row['status'] in ('completed','child_failed') and not row['remaining_live_group_pids']
        terminal.append(dict(path=str(p.relative_to(stage)),status=row['status']))
    files={str(p.relative_to(stage)):sha(p) for p in stage.rglob('*') if p.is_file()}
    archive=stage/'checkpoint.tar.gz';manifest=(json.dumps(files,sort_keys=True,indent=2)+'\n').encode()
    with tarfile.open(archive,'w:gz') as tar:
        info=tarfile.TarInfo('member-manifest.json');info.size=len(manifest);tar.addfile(info,io.BytesIO(manifest))
        for name in sorted(files):tar.add(stage/name,arcname='evidence/'+name,recursive=False)
    restored=stage/'recovered';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==len(files)+1
        for member in members:
            assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
            if member.name=='member-manifest.json':continue
            assert member.name.startswith('evidence/');name=member.name[len('evidence/'):]
            raw=tar.extractfile(member).read();assert hashlib.sha256(raw).hexdigest()==files[name]
            dest=restored/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            if member.mode&0o111:dest.chmod(0o755)
    for name,digest in files.items():assert sha(restored/name)==digest
    empty=restored/'empty-cwd';empty.mkdir()
    environment=dict(os.environ,PYTHONPATH=str(restored/'copied-source'))
    commands=[
        [sys.executable,'-B','-m','experiments.rc_completion_reachability.analysis.prefix','prepare',
         '--stage',str(restored),'--output',str(restored/'regenerated')],
        [str(restored/'prefix/prefix')],
        [sys.executable,'-B','-m','experiments.rc_completion_reachability.analysis.verify',
         '--stage',str(restored),'--output',str(restored/'reverification.json')],
        [sys.executable,'-B','-m','experiments.rc_completion_reachability.analysis.report',
         '--stage',str(restored),'--output',str(restored/'reanalysis')]]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,env=environment,capture_output=True,timeout=60)
        (stage/f'recovery-{i}.stdout').write_bytes(result.stdout);(stage/f'recovery-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr)
        if i==1:assert result.stdout==(stage/'prefix/records.jsonl').read_bytes()
    for name in ('prefix.c','fragments.json'):assert (restored/'regenerated'/name).read_bytes()==(stage/'prefix'/name).read_bytes()
    assert (restored/'reverification.json').read_bytes()==(stage/'verification.json').read_bytes()
    for p in (stage/'analysis').iterdir():assert (restored/'reanalysis'/p.name).read_bytes()==p.read_bytes()
    save(stage/'recovery.json',dict(passed=True,archive_sha256=sha(archive),archive_members=len(files)+1,
         member_hashes_verified=True,copied_source_empty_cwd=True,source_regeneration_byte_equal=True,
         existing_binary_records_byte_equal=True,verification_report_summary_byte_equal=True,
         terminal_monitors_in_archive=terminal,HTTP=0,builds=0,network_runs=0,GPU=0,commands=commands,
         scope='same Linux host CPU binary and source recovery, not arbitrary ISA/libc portability'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
