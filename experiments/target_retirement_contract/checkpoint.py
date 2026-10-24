"""Create or validate/restore a bounded source-audit package; never execute on restore."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


MAX_BYTES=16*2**20
MAX_MEMBERS=512
SCHEMA='target-retirement-checkpoint/1'


def sha(data):return hashlib.sha256(data).hexdigest()


def safe(name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p)!=name or len(p.parts)<2:
        raise ValueError('unsafe member name')
    if p.parts[0] not in ('source','evidence'):raise ValueError('unknown member root')


def pack(stage,archive):
    root=Path(__file__).resolve().parents[2];files={}
    directories=('inputs','sources','original','build','acquire-monitor','api-monitor',
        'umr-monitor','prepare-monitor','build-monitor','source-run-monitor',
        'analyze-monitor','analyze-corrected-monitor','analyze-excerpts-monitor',
        'verify-monitor','correction-monitor')
    paths=[]
    for name in directories:paths.extend((stage/name).rglob('*'))
    for name in ('summary.json','verification.json','correction.json','acquisition.json',
            'api-acquisition.json','umr-acquisition.json','linux-commit.txt',
            'acquire.py','acquire_api.py','acquire_umr.py'):
        paths.append(stage/name)
    for path in sorted(set(paths)):
        if path.is_symlink():raise ValueError('symlink in evidence')
        if path.is_file():files['evidence/'+path.relative_to(stage).as_posix()]=path.read_bytes()
    for path in sorted(Path(__file__).resolve().parent.iterdir()):
        if path.is_file() and path.suffix in ('.py','.md','.c','.h'):
            files['source/'+path.relative_to(root).as_posix()]=path.read_bytes()
    for name in ('docs/reviews/目标内存撤销与恢复强基线_20261024.md',
            'docs/review_artifacts/nccl_gin_20261022/sources.json',
            'docs/review_artifacts/deepep_modern_20261022/sources.json',
            'data/research/rc_atomic_20261022/sources.json'):
        files['source/'+name]=(root/name).read_bytes()
    for name in files:safe(name)
    manifest=dict(schema_version=SCHEMA,files={n:dict(bytes=len(v),sha256=sha(v)) for n,v in sorted(files.items())})
    files['manifest.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    assert len(files)<=MAX_MEMBERS and sum(map(len,files.values()))<=MAX_BYTES
    with archive.open('xb') as output,tarfile.open(fileobj=output,mode='w:gz') as tar:
        for name,data in sorted(files.items()):
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644
            tar.addfile(item,io.BytesIO(data))
    assert archive.stat().st_size<=MAX_BYTES
    print(json.dumps(dict(schema_version=SCHEMA,members=len(files),
        plain_bytes=sum(map(len,files.values())),archive_bytes=archive.stat().st_size,
        archive_sha256=sha(archive.read_bytes()),executes_on_restore=False)))


def restore(archive,destination):
    if destination.exists():raise ValueError('destination must not exist')
    if archive.stat().st_size>MAX_BYTES:raise ValueError('compressed size limit')
    files={};total=0
    with tarfile.open(archive,'r:gz') as tar:
        for item in tar:
            if item.name!='manifest.json':safe(item.name)
            if not item.isfile() or item.name in files:raise ValueError('nonregular or duplicate member')
            total+=item.size
            if item.size<0 or total>MAX_BYTES or len(files)>=MAX_MEMBERS:raise ValueError('package limit')
            files[item.name]=tar.extractfile(item).read()
    manifest=json.loads(files.pop('manifest.json'))
    if manifest['schema_version']!=SCHEMA or set(files)!=set(manifest['files']):raise ValueError('manifest mismatch')
    for name,data in files.items():
        if manifest['files'][name]!=dict(bytes=len(data),sha256=sha(data)):raise ValueError('checksum mismatch')
    destination.mkdir(parents=False)
    for name,data in files.items():
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as output:output.write(data)
    print(json.dumps(dict(restored=len(files),all_members_hash_verified=True,executes_on_restore=False)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('pack','restore'))
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--stage',type=Path);p.add_argument('--destination',type=Path)
    a=p.parse_args()
    if a.mode=='pack':
        if a.stage is None:p.error('pack needs --stage')
        pack(a.stage.resolve(),a.archive.resolve())
    else:
        if a.destination is None:p.error('restore needs --destination')
        restore(a.archive.resolve(),a.destination.resolve())


if __name__=='__main__':main()
