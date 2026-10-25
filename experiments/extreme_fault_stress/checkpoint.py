"""Hash-check bounded evidence archives and replay analysis without networks."""
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


def digest(data):return hashlib.sha256(data).hexdigest()


def create(stage, repo, output):
    assert not output.exists();output.parent.mkdir(parents=True,exist_ok=True)
    files={}
    for p in stage.rglob('*'):
        rel=p.relative_to(stage)
        if p.is_file() and rel.parts[0] not in ('archive-monitor','restored'):
            assert not p.is_symlink();files['evidence/'+rel.as_posix()]=p.read_bytes()
    for p in (repo/'experiments/extreme_fault_stress').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            assert not p.is_symlink();files['source/'+p.relative_to(repo).as_posix()]=p.read_bytes()
    assert len(files)<2048 and sum(map(len,files.values()))<96*2**20
    assert max(map(len,files.values()))<=32*2**20
    manifest=dict(schema='moe-stress-checkpoint/1',files={n:dict(bytes=len(b),sha256=digest(b)) for n,b in files.items()},
                  native_runtime_bundled=False,actual_networks=3,network_phase_closed=True)
    files['manifest.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    with tarfile.open(output,'w:gz') as archive:
        for name,blob in sorted(files.items()):
            info=tarfile.TarInfo(name);info.size=len(blob);info.mode=0o644;info.mtime=0;archive.addfile(info,io.BytesIO(blob))
    receipt=dict(sha256=digest(output.read_bytes()),members=len(files),uncompressed_bytes=sum(map(len,files.values())),
                 compressed_bytes=output.stat().st_size,native_runtime_bundled=False)
    output.with_suffix(output.suffix+'.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


def restore(archive_path, out):
    assert not out.exists()
    with tarfile.open(archive_path,'r:gz') as archive:
        members=archive.getmembers();assert 1<len(members)<=2048
        assert len({x.name for x in members})==len(members)
        for m in members:
            p=PurePosixPath(m.name)
            assert m.isfile() and not m.islnk() and not m.issym() and not p.is_absolute()
            assert '..' not in p.parts and '\\' not in m.name and 0<=m.size<=32*2**20
        assert sum(m.size for m in members)<=96*2**20
        blobs={m.name:archive.extractfile(m).read() for m in members}
    manifest=json.loads(blobs.pop('manifest.json'))
    assert set(manifest['files'])==set(blobs)
    for name,b in blobs.items():assert len(b)==manifest['files'][name]['bytes'] and digest(b)==manifest['files'][name]['sha256']
    out.mkdir()
    for name,b in blobs.items():
        p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    empty=out/'empty';empty.mkdir();source=out/'source';evidence=out/'evidence'
    env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1')
    commands=[['analyze.py','--stage',evidence,'--inputs',evidence/'inputs-v3','--output',out/'summary.json'],
              ['verify.py','--stage',evidence,'--inputs',evidence/'inputs-v3','--analysis',out/'summary.json','--output',out/'verification.json']]
    logs=[]
    for command in commands:
        cmd=[sys.executable,'-B',source/'experiments/extreme_fault_stress'/command[0],*command[1:]]
        r=subprocess.run(list(map(str,cmd)),cwd=empty,env=env,text=True,capture_output=True,timeout=60)
        logs.append(dict(command=list(map(str,cmd)),cwd=str(empty),exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        assert r.returncode==0,r.stderr
    equal={name:(out/name).read_bytes()==(evidence/name).read_bytes() for name in ('summary.json','verification.json')}
    assert all(equal.values())
    report=dict(passed=True,archive_sha256=digest(archive_path.read_bytes()),members_hash_verified=True,
                copied_research_root=True,empty_cwd=True,byte_identical=equal,new_networks=0,native_runtime_required=False,commands=logs)
    (out/'replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='commands'}))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('create','restore'))
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--output',type=Path);p.add_argument('--stage',type=Path);p.add_argument('--repository',type=Path)
    a=p.parse_args()
    if a.action=='create':assert a.stage and a.repository;create(a.stage.resolve(),a.repository.resolve(),a.archive.resolve())
    else:assert a.output;restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
