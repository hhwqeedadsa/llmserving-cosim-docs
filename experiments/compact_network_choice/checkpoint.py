"""Bounded per-member hash checkpoint; offline copied-source reproduction."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

from experiments.compact_network_choice.prepare import load, save, sha


def admit(path,digest):
    assert path.stat().st_size<=64*2**20 and sha(path)==digest
    blobs={};total=0
    with tarfile.open(path,'r:gz') as archive:
        for item in archive:
            p=PurePosixPath(item.name)
            assert item.isfile() and not p.is_absolute() and '..' not in p.parts and '\\' not in item.name
            assert str(p)==item.name and item.name not in blobs and len(blobs)<4096
            assert 0<=item.size<=16*2**20
            total+=item.size;assert total<256*2**20
            raw=archive.extractfile(item).read();assert len(raw)==item.size;blobs[item.name]=raw
    manifest=json.loads(blobs.pop('manifest.json'));assert set(blobs)==set(manifest['files'])
    assert not any(str(p) in blobs for name in blobs for p in PurePosixPath(name).parents if str(p)!='.')
    for name,raw in blobs.items():
        assert manifest['files'][name]==dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return blobs


def create(repo,stage,record,output):
    assert not output.exists() and load(stage/'verification.json')['passed']
    assert load(stage/'execution.json')['closed']
    files={'evidence/'+str(p.relative_to(stage)):p for p in stage.rglob('*') if p.is_file()}
    for p in (repo/'experiments/compact_network_choice').glob('*'):
        if p.is_file() and p.suffix in ('.py','.md'):files['source/'+str(p.relative_to(repo))]=p
    for name in ('prepare.py','diagnose_event_graph.py','compile_event_graph.py','event_graph.py'):
        p=repo/'experiments/static_dag_transfer'/name;files['source/'+str(p.relative_to(repo))]=p
    for p in (stage/'final-report').iterdir():files['expected/'+p.name]=p
    for directory in record.glob('*-monitor'):
        probe=directory/'probe.json'
        if probe.is_file() and load(probe)['status'] in ('completed','child_failed'):
            for p in directory.iterdir():
                if p.is_file():files['monitor/'+str(p.relative_to(record))]=p
    assert not any(p.is_symlink() for p in files.values())
    entries={n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in files.items()}
    assert len(entries)<4095 and max(v['bytes'] for v in entries.values())<=16*2**20
    assert sum(v['bytes'] for v in entries.values())<256*2**20
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,p in sorted(files.items()):
            raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o644;archive.addfile(info,io.BytesIO(raw))
        raw=(json.dumps(dict(schema_version='compact-choice-checkpoint/1',files=entries),indent=2)+'\n').encode()
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
    digest=sha(output);admit(output,digest)
    save(output.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=output.stat().st_size,uncompressed_bytes=sum(v['bytes'] for v in entries.values())))


def restore(archive,output):
    assert not output.exists();digest=load(archive.with_suffix('.receipt.json'))['sha256']
    blobs=admit(archive,digest);output.mkdir()
    for name,raw in blobs.items():
        dest=output/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    empty=output/'empty-cwd';empty.mkdir()
    # Prediction subprocess has no truth sibling: only copied observed inputs.
    observed_only=output/'observed-only';observed_only.mkdir()
    shutil.copytree(output/'evidence/observed',observed_only/'observed')
    env=os.environ|{'PYTHONPATH':str(output/'source'),'PYTHONDONTWRITEBYTECODE':'1'}
    commands=[
        [sys.executable,'-B','-m','experiments.compact_network_choice.compact','--observed',str(observed_only/'observed'),
         '--output',str(observed_only/'predictions')],
        [sys.executable,'-B','-m','experiments.compact_network_choice.evaluate','report','--stage',str(output/'evidence'),
         '--output',str(output/'reanalysis')]]
    rows=[]
    for cmd in commands:
        done=subprocess.run(cmd,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
        rows.append(dict(command=cmd,cwd=str(empty),exit_code=done.returncode,stderr=done.stderr));save(output/'commands.json',rows)
        assert done.returncode==0,done.stderr
    same={n:(output/'reanalysis'/n).read_bytes()==blobs['expected/'+n] for n in ('summary.json','report.md')}
    predictions={p.name:p.read_bytes()==blobs['evidence/predictions/'+p.name] for p in (observed_only/'predictions').iterdir()}
    assert all(same.values()) and len(predictions)==2 and all(predictions.values())
    save(output/'recovery.json',dict(passed=True,archive_sha256=digest,hash_verified_members=len(blobs),
        copied_source=True,empty_cwd=True,byte_identical=same,prediction_byte_identical=predictions,new_networks=0))


def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True)
    c=s.add_parser('create')
    for n in ('repository','stage','record','output'):c.add_argument('--'+n,type=Path,required=True)
    r=s.add_parser('restore')
    for n in ('archive','output'):r.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.record.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
