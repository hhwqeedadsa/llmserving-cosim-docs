"""Hash archive and copied-source offline analysis; no builds/network/GPU."""
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

from experiments.input_graph_baseline.analysis.study import load,save,sha


def execute(repo,stage):
    copied=stage/'copied-source';assert not copied.exists()
    for folder in ('experiments/input_graph_baseline','python/moe_ub_cosim'):
        for source in (repo/folder).rglob('*.py'):
            assert not source.is_symlink()
            dest=copied/source.relative_to(repo);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
    shutil.copytree(Path(str(stage)+'-prepare-monitor'),stage/'prepare-monitor')
    for p in stage.rglob('probe.json'):
        row=load(p);assert row['status']=='completed' and not row['remaining_live_group_pids']
    files={str(p.relative_to(stage)):dict(sha256=sha(p),bytes=p.stat().st_size)
           for p in stage.rglob('*') if p.is_file()}
    assert len(files)<=2048 and sum(v['bytes'] for v in files.values())<=64*2**20
    archive=stage/'checkpoint.tar.gz';raw=(json.dumps(files,sort_keys=True,indent=2)+'\n').encode()
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('manifest.json');item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
        for name in sorted(files):tar.add(stage/name,arcname='evidence/'+name,recursive=False)
    restored=stage/'recovered';restored.mkdir()
    with tarfile.open(archive,'r:gz') as tar:
        members=tar.getmembers();assert len(members)==len(files)+1
        assert len({m.name for m in members})==len(members)
        assert tar.extractfile('manifest.json').read()==raw
        for member in members:
            assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
            if member.name=='manifest.json':continue
            assert member.name.startswith('evidence/');name=member.name[len('evidence/'):]
            assert name in files and member.size==files[name]['bytes']
            body=tar.extractfile(member).read();assert hashlib.sha256(body).hexdigest()==files[name]['sha256']
            dest=restored/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(body)
    for name,row in files.items():assert sha(restored/name)==row['sha256']
    empty=restored/'empty-cwd';empty.mkdir()
    env=dict(os.environ,PYTHONPATH=str(restored/'copied-source/python')+os.pathsep+str(restored/'copied-source'))
    common=[sys.executable,'-B','-m']
    commands=[
        common+['experiments.input_graph_baseline.analysis.report','recompute','--stage',str(restored),'--output',str(restored/'recomputed.json')],
        common+['experiments.input_graph_baseline.analysis.verify','--stage',str(restored),'--output',str(restored/'reverification.json')],
        common+['experiments.input_graph_baseline.analysis.report','report','--stage',str(restored),'--output',str(restored/'reanalysis')]]
    for i,command in enumerate(commands):
        completed=subprocess.run(command,cwd=empty,env=env,capture_output=True,timeout=60)
        (stage/f'recovery-{i}.stdout').write_bytes(completed.stdout);(stage/f'recovery-{i}.stderr').write_bytes(completed.stderr)
        assert completed.returncode==0,(i,completed.stderr)
    assert (restored/'recomputed.json').read_bytes()==(stage/'recomputed.json').read_bytes()
    assert (restored/'reverification.json').read_bytes()==(stage/'verification-source-selection.json').read_bytes()
    for p in (stage/'analysis').iterdir():assert p.read_bytes()==(restored/'reanalysis'/p.name).read_bytes()
    save(stage/'recovery.json',dict(passed=True,archive_members=len(files)+1,archive_sha256=sha(archive),
        member_hashes_verified=True,copied_source_empty_cwd=True,derived_graphs_byte_equal=True,
        source_selection_and_verification_byte_equal=True,summary_report_byte_equal=True,
        HTTP=0,builds=0,network_runs=0,GPU=0,commands=commands))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.stage.resolve())
