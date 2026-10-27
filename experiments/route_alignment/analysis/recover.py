"""Content-hash archive and copied-source/empty-cwd offline recovery only."""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from experiments.route_alignment.analysis.common import load, save, sha


def execute(repo,stage):
    source=stage/'copied-source';source.mkdir()
    names=[]
    for path in (repo/'experiments/route_alignment').rglob('*.py'):names.append(path.relative_to(repo))
    names.append(Path('experiments/ecmp_baseline_audit/model.py'))
    for name in names:
        dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repo/name,dest)
    files={str(p.relative_to(stage)):sha(p) for p in stage.rglob('*') if p.is_file()}
    # All monitored inputs are terminal before this command. This command's
    # own monitor must be outside stage so it is not a changing archive member.
    for p in stage.rglob('probe.json'):
        record=load(p);assert record['status'] in ('completed','child_failed')
        assert not record['remaining_live_group_pids']
    archive=stage/'checkpoint.tar.gz'
    manifest=(__import__('json').dumps(files,sort_keys=True,indent=2)+'\n').encode()
    with tarfile.open(archive,'w:gz') as tar:
        info=tarfile.TarInfo('member-manifest.json');info.size=len(manifest);tar.addfile(info,io.BytesIO(manifest))
        for name in sorted(files):tar.add(stage/name,arcname='evidence/'+name,recursive=False)
    restored=stage/'recovered';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==len(files)+1
        for member in members:
            assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
            if member.name=='member-manifest.json':continue
            assert member.name.startswith('evidence/')
            name=member.name[len('evidence/'):];raw=tar.extractfile(member).read()
            assert hashlib.sha256(raw).hexdigest()==files[name]
            dest=restored/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    for name,digest in files.items():assert sha(restored/name)==digest
    empty=restored/'empty-cwd';empty.mkdir()
    copied=restored/'copied-source'
    environment=dict(__import__('os').environ,PYTHONPATH=str(copied))
    commands=[
        [sys.executable,'-B','-m','experiments.route_alignment.analysis.close','--stage',str(restored),'--output',str(restored/'reanalysis')],
        [sys.executable,'-B','-m','experiments.route_alignment.tests.check'],
        [sys.executable,'-B','-c','from pathlib import Path;from experiments.route_alignment.analysis.prepare import predictions;predictions(Path(__import__("sys").argv[1]),Path(__import__("sys").argv[2]))',str(restored/'observed'),str(restored/'repredicted')]]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=empty,env=environment,capture_output=True,timeout=60)
        (stage/f'recovery-{i}.stdout').write_bytes(result.stdout);(stage/f'recovery-{i}.stderr').write_bytes(result.stderr)
        assert result.returncode==0,(i,result.stderr)
    for p in (stage/'analysis').iterdir():assert p.read_bytes()==(restored/'reanalysis'/p.name).read_bytes(),p.name
    for p in (stage/'predictions').iterdir():assert p.read_bytes()==(restored/'repredicted'/p.name).read_bytes(),p.name
    # A second prediction with truth absent establishes that prediction only
    # consumes observed input; no network execution is part of recovery.
    hidden=restored/'hidden-truth';(restored/'truth').rename(hidden)
    command=commands[-1][:-1]+[str(restored/'prediction-without-truth')]
    result=subprocess.run(command,cwd=empty,env=environment,capture_output=True,timeout=60)
    (stage/'recovery-no-truth.stdout').write_bytes(result.stdout);(stage/'recovery-no-truth.stderr').write_bytes(result.stderr)
    assert result.returncode==0,result.stderr
    for p in (stage/'predictions').iterdir():assert p.read_bytes()==(restored/'prediction-without-truth'/p.name).read_bytes()
    save(stage/'recovery.json',dict(passed=True,archive_sha256=sha(archive),archive_members=len(files)+1,
        member_hashes_verified=True,copied_source_empty_cwd=True,report_and_summary_byte_equal=True,
        predictions_byte_equal=True,prediction_without_truth_byte_equal=True,
        repeated_small_input_invariants=True,network_runs=0,builds=0,HTTP=0,GPU=0,
        commands=commands+[command]))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
