"""Bounded terminal evidence package; verify every member before restoration."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

MAX_BYTES=64*2**20
MAX_FILE=24*2**20


def sha(data):return hashlib.sha256(data).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def create(repo,s,output):
    files={}
    for name in ('python','backend','fixtures/transaction_selective_payload','examples/scaleout_rdma_transactions','examples/scaleout_rdma_streams'):
        for p in (repo/name).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.cc','.hh','.md','.json'):
                files['source/'+p.relative_to(repo).as_posix()]=p
    for name in ('deps.lock.json','docs/tasks/transaction_selective_payload_spec.md','docs/record_storage.md',
                 'docs/reviews/选择重传事务交付与应用接入边界_20261024.md'):
        files['source/'+name]=repo/name
    for p in (s/'lib').iterdir():files['runtime/lib/'+p.name]=p
    for name in ('transaction-probe','transaction-session','stream-probe'):
        files['runtime/bin/'+name]=s/'bin'/name
    for name in ('inputs','topology','network','native-rejections','fresh','archive-safety','reference','checks'):
        if not (s/name).exists():continue
        for p in (s/name).rglob('*'):
            if p.is_file():files['evidence/'+p.relative_to(s).as_posix()]=p
    # Live monitor directories have no terminal receipt and are not packaged.
    for receipt in s.glob('*-monitor/probe.json'):
        probe=json.loads(receipt.read_text())
        assert 'ended_utc' in probe and not probe['remaining_live_group_pids']
        for p in receipt.parent.iterdir():
            if p.is_file():files['evidence/'+p.relative_to(s).as_posix()]=p
    for p in s.iterdir():
        if p.is_file() and p.suffix in ('.json','.txt'):files['evidence/'+p.name]=p
    entries={};total=0
    for name,p in sorted(files.items()):
        assert not p.is_symlink();data=p.read_bytes();assert len(data)<=MAX_FILE
        entries[name]=dict(bytes=len(data),sha256=sha(data));total+=len(data)
    manifest=dict(schema_version='transaction-selective-checkpoint/1',files=entries,bytes=total,
        scope='same-Linux-host incremental runtime; no clean native build, GPU or modern GIN claim')
    raw=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
    assert total+len(raw)<=MAX_BYTES and len(entries)<3000
    with tarfile.open(output,'x:gz') as archive:
        for name,p in sorted(files.items()):
            data=p.read_bytes();assert sha(data)==entries[name]['sha256'] and len(data)==entries[name]['bytes']
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if name.startswith('runtime/bin/') else 0o644
            archive.addfile(info,io.BytesIO(data))
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
    save(output.with_suffix('.receipt.json'),dict(archive_bytes=output.stat().st_size,
        sha256=sha(output.read_bytes()),members=len(files)+1,uncompressed_bytes=total))


def restore(archive_path,output,verify_only=False):
    if not verify_only and output.exists():raise ValueError('restore target exists')
    if archive_path.stat().st_size>MAX_BYTES:raise ValueError('compressed byte limit')
    with tarfile.open(archive_path,'r:gz') as archive:
        members=archive.getmembers()
        if len(members)>3000 or sum(m.size for m in members)>MAX_BYTES:raise ValueError('archive aggregate limit')
        names=set()
        for m in members:
            p=PurePosixPath(m.name)
            if not m.isfile() or m.name in names or p.is_absolute() or '..' in p.parts or '\\' in m.name or str(p)!=m.name or m.name in ('','.','manifest.json/') or not 0<=m.size<=MAX_FILE:
                raise ValueError('unsafe archive member')
            names.add(m.name)
        for name in names:
            if any(str(p) in names for p in PurePosixPath(name).parents if str(p)!='.'):
                raise ValueError('file is also a parent')
        if 'manifest.json' not in names:raise ValueError('missing manifest')
        manifest=json.loads(archive.extractfile('manifest.json').read())
        if manifest.get('schema_version')!='transaction-selective-checkpoint/1' or set(manifest['files'])!=names-{'manifest.json'}:
            raise ValueError('manifest membership mismatch')
        data={}
        for name,entry in manifest['files'].items():
            raw=archive.extractfile(name).read()
            if len(raw)!=entry['bytes'] or sha(raw)!=entry['sha256']:raise ValueError('member hash mismatch')
            data[name]=raw
        if sum(len(v) for v in data.values())!=manifest['bytes']:raise ValueError('manifest byte count')
    if verify_only:return manifest
    output.mkdir()
    for name,raw in data.items():
        dest=output/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        dest.chmod(0o755 if name.startswith('runtime/bin/') else 0o644)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('create','restore','verify'));p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--repository',type=Path);p.add_argument('--stage',type=Path);p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.archive)
    else:restore(a.archive,a.output,verify_only=a.action=='verify')


if __name__=='__main__':main()
