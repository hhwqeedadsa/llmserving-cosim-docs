"""Offline-only hashed evidence bundle; never silently executes a network."""
import argparse
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import tarfile


def sha(data):return hashlib.sha256(data).hexdigest()
def safe_name(name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p)!=name or not p.parts:raise ValueError('unsafe archive name')
    return p


def pack(stage,archive):
    repo=Path(__file__).resolve().parents[2];files={}
    excluded={'restored','empty-cwd','checkpoint-monitor','restore-monitor','offline-monitor','persist-monitor','checkpoint.tar.gz','checkpoint-receipt.json','closeout.json'}
    for p in sorted(stage.rglob('*')):
        rel=p.relative_to(stage)
        if rel.parts[0] in excluded:continue
        if p.is_symlink():raise ValueError('symlink in evidence')
        if p.is_file():files['evidence/'+str(rel)]=p.read_bytes()
    for p in sorted((repo/'experiments/late_write_isolation').iterdir()):
        if p.is_file() and (p.suffix=='.py' or p.suffix=='.md'):
            files['source/'+str(p.relative_to(repo))]=p.read_bytes()
    manifest=dict(schema_version='late-write-offline-checkpoint/1',network_replay_included=False,
        files={name:dict(bytes=len(data),sha256=sha(data)) for name,data in files.items()})
    files['checkpoint-manifest.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    if archive.exists():raise ValueError('archive already exists')
    with tarfile.open(archive,'w:gz') as tar:
        for name,data in files.items():
            info=tarfile.TarInfo(name);info.size=len(data);info.mtime=0;info.mode=0o644;tar.addfile(info,io.BytesIO(data))
    print(json.dumps(dict(members=len(files),plain_bytes=sum(map(len,files.values())),compressed_bytes=archive.stat().st_size,sha256=sha(archive.read_bytes()))))


def restore(archive,destination):
    if destination.exists():raise ValueError('restore destination already exists')
    files={};total=0
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            safe_name(item.name)
            if not item.isfile() or item.name in files:raise ValueError('nonregular or duplicate archive member')
            total+=item.size
            if item.size<0 or total>64*2**20:raise ValueError('archive size limit')
            files[item.name]=tar.extractfile(item).read()
    manifest=json.loads(files.pop('checkpoint-manifest.json'))
    if set(files)!=set(manifest['files']):raise ValueError('manifest coverage mismatch')
    for name,data in files.items():
        if manifest['files'][name]!=dict(bytes=len(data),sha256=sha(data)):raise ValueError('member checksum mismatch')
    destination.mkdir()
    for name,data in files.items():
        p=destination/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    print(json.dumps(dict(restored=len(files),destination=str(destination),network_executed=False)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['pack','restore'])
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--stage',type=Path);p.add_argument('--destination',type=Path)
    a=p.parse_args()
    if a.mode=='pack':pack(a.stage.resolve(),a.archive.resolve())
    else:restore(a.archive.resolve(),a.destination.resolve())


if __name__=='__main__':main()
