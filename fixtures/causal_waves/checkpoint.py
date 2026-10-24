"""Source/evidence snapshot; native runtime remains an explicit hashed input."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

MAX_BYTES=32*2**20
MAX_FILE=8*2**20


def sha(data):return hashlib.sha256(data).hexdigest()


def create(repo,stage,output):
    files={}
    for name in ('python','examples/scaleout_causal_waves','fixtures/causal_waves'):
        for p in (repo/name).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.json','.md'):
                files['source/'+p.relative_to(repo).as_posix()]=p
    for name in ('deps.lock.json','docs/tasks/causal_wave_spec.md','tests/unit/scaleout_causal_waves_test.py'):
        files['source/'+name]=repo/name
    report=repo/'docs/reviews/完整前端图与持久恢复基线接入_20261024.md'
    if report.exists():files['source/'+report.relative_to(repo).as_posix()]=report
    for directory in ('inputs','topology','planned','network','fresh','archive-safety'):
        if (stage/directory).exists():
            for p in (stage/directory).rglob('*'):
                if p.is_file():files['evidence/'+p.relative_to(stage).as_posix()]=p
    for p in stage.glob('*-monitor/probe.json'):
        r=json.loads(p.read_text());assert 'ended_utc' in r and not r['remaining_live_group_pids']
        for f in p.parent.iterdir():
            if f.is_file():files['evidence/'+f.relative_to(stage).as_posix()]=f
    for p in stage.iterdir():
        if p.is_file() and p.suffix=='.json':files['evidence/'+p.name]=p
    entries={}
    for name,p in sorted(files.items()):
        assert not p.is_symlink();raw=p.read_bytes();assert len(raw)<=MAX_FILE
        entries[name]=dict(bytes=len(raw),sha256=sha(raw))
    manifest=dict(schema_version='causal-wave-checkpoint/1',files=entries,bytes=sum(x['bytes'] for x in entries.values()),
        scope='source and evidence only; requires the recorded existing native binary and eight libraries; no clean build')
    raw=json.dumps(manifest,indent=2,sort_keys=True).encode()+b'\n'
    assert manifest['bytes']+len(raw)<=MAX_BYTES and len(entries)<3000
    with tarfile.open(output,'x:gz') as tar:
        for name,p in sorted(files.items()):
            data=p.read_bytes();assert sha(data)==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);tar.addfile(info,io.BytesIO(raw))
    receipt=dict(bytes=output.stat().st_size,sha256=sha(output.read_bytes()),members=len(entries)+1,uncompressed_bytes=manifest['bytes'])
    output.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')


def restore(path,output=None,verify_only=False):
    if not verify_only and output.exists():raise ValueError('output exists')
    if path.stat().st_size>MAX_BYTES:raise ValueError('compressed byte limit')
    with tarfile.open(path,'r:gz') as tar:
        members=tar.getmembers()
        if len(members)>3000 or sum(m.size for m in members)>MAX_BYTES:raise ValueError('aggregate limit')
        names=set()
        for m in members:
            p=PurePosixPath(m.name)
            if not m.isfile() or m.name in names or p.is_absolute() or '..' in p.parts or '\\' in m.name or str(p)!=m.name or str(p) in ('','.') or not 0<=m.size<=MAX_FILE:
                raise ValueError('unsafe member')
            names.add(m.name)
        if any(str(p) in names for n in names for p in PurePosixPath(n).parents if str(p)!='.'):
            raise ValueError('file parent conflict')
        if 'manifest.json' not in names:raise ValueError('missing manifest')
        manifest=json.loads(tar.extractfile('manifest.json').read())
        if manifest.get('schema_version')!='causal-wave-checkpoint/1' or set(manifest['files'])!=names-{'manifest.json'}:
            raise ValueError('manifest mismatch')
        data={}
        for name,entry in manifest['files'].items():
            raw=tar.extractfile(name).read()
            if len(raw)!=entry['bytes'] or sha(raw)!=entry['sha256']:raise ValueError('hash mismatch')
            data[name]=raw
        if sum(map(len,data.values()))!=manifest['bytes']:raise ValueError('byte count')
    if verify_only:return manifest
    output.mkdir()
    for name,raw in data.items():
        p=output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('create','restore','verify'))
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--repository',type=Path);p.add_argument('--stage',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.archive)
    else:restore(a.archive,a.output,verify_only=a.action=='verify')


if __name__=='__main__':main()
