"""Bounded source/runtime/evidence bundle; pinned host libraries still required."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[2]
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--extract',choices=['lib','rest'])
    p.add_argument('--destination',type=Path)
    a=p.parse_args();r=a.runtime.resolve();out=r/'checkpoint'
    if a.extract:
        assert a.destination is not None
        dest=a.destination.resolve();receipt=json.loads((out/'archive.json').read_text())
        assert sha(out/'checkpoint.tar.gz')==receipt['sha256']
        with tarfile.open(out/'checkpoint.tar.gz','r:gz') as tf:
            manifest=json.load(tf.extractfile('manifest.json'));selected=[]
            for m in tf:
                if m.name=='manifest.json':continue
                path=Path(m.name);assert not path.is_absolute() and '..' not in path.parts and m.isfile()
                if (path.parts[0]=='lib')!=(a.extract=='lib'):continue
                assert not (dest/path).exists()
                tf.extract(m,path=dest,filter='data');selected.append(m.name)
            for name in selected:assert sha(dest/name)==manifest[name]['sha256']
        report=dict(passed=True,part=a.extract,members=len(selected),
                    bytes=sum(manifest[n]['bytes'] for n in selected))
        (r/('restore-'+a.extract+'.json')).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report));return
    out.mkdir();files={}
    for folder in ['python','backend','examples','tests','fixtures/source_stream_isolation']:
        for q in (ROOT/folder).rglob('*'):
            if q.is_file() and not q.is_symlink() and '__pycache__' not in q.parts and q.suffix!='.pyc':
                files['source/'+str(q.relative_to(ROOT))]=q
    for rel in ['AGENTS.md','README.md','deps.lock.json','docs/development.md',
                'docs/tasks/source_stream_isolation_spec.md','docs/record_storage.md']:
        files['source/'+rel]=ROOT/rel
    for q in r.rglob('*'):
        if not q.is_file() or q.is_symlink():continue
        rel=q.relative_to(r)
        if rel.parts[0] in ['checkpoint','checkpoint-monitor','restored','restore-lib-monitor','restore-rest-monitor']:continue
        if 'objects' in rel.parts:continue
        if rel.parts[:2]==('build','bin') and q.name!='ns3.42-moe-rdma-stream-probe':continue
        files['artifacts/'+str(rel)]=q
    libs=list(Path('/dev/shm/moe-fault-20261021/native/output/lib').glob('libns3*.so'))
    assert len(libs)==8
    for q in libs:files['lib/'+q.name]=q
    manifest={name:dict(bytes=q.stat().st_size,sha256=sha(q)) for name,q in sorted(files.items())}
    assert sum(x['bytes'] for x in manifest.values())<96*2**20
    raw=(json.dumps(manifest,indent=2)+'\n').encode()
    archive=out/'checkpoint.tar.gz'
    with tarfile.open(archive,'w:gz',compresslevel=6) as tf:
        info=tarfile.TarInfo('manifest.json');info.size=len(raw);tf.addfile(info,io.BytesIO(raw))
        for name,q in sorted(files.items()):tf.add(q,arcname=name,recursive=False)
    assert archive.stat().st_size<32*2**20
    with tarfile.open(archive,'r:gz') as tf:
        assert json.load(tf.extractfile('manifest.json'))==manifest
        for m in tf:
            if m.name=='manifest.json':continue
            assert hashlib.file_digest(tf.extractfile(m),'sha256').hexdigest()==manifest[m.name]['sha256']
    assert all(sha(q)==manifest[name]['sha256'] for name,q in files.items())
    receipt=dict(passed=True,sha256=sha(archive),archive_bytes=archive.stat().st_size,
        plain_bytes=sum(x['bytes'] for x in manifest.values()),members=len(manifest),
        bundled_ns3_libraries=8,scope='source and prebuilt runtime replay; not offline third-party rebuild')
    (out/'manifest.json').write_bytes(raw)
    (out/'archive.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))

if __name__=='__main__':main()
