"""Bounded source/runtime/evidence archive with pre-write path/hash admission."""
import argparse
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import tarfile
from fixtures.context_transactions.stage import load,save,sha

SCHEMA='context-transactions-checkpoint/1'


def admit(path):
    assert path.stat().st_size<32*2**20
    content={};total=0
    with tarfile.open(path,'r:gz') as ar:
        for m in ar:
            p=PurePosixPath(m.name)
            assert len(content)<2048 and m.isfile() and not p.is_absolute() and '..' not in p.parts
            assert str(p)==m.name and '\\' not in m.name and m.name not in ('','.') and m.name not in content
            assert 0<=m.size<=16*2**20;total+=m.size;assert total<128*2**20
            content[m.name]=ar.extractfile(m).read();assert len(content[m.name])==m.size
    assert not any(str(p) in content for name in content for p in PurePosixPath(name).parents if str(p)!='.')
    manifest=json.loads(content['manifest.json']);assert manifest['schema_version']==SCHEMA
    assert set(manifest['files'])==set(content)-{'manifest.json'}
    for name,e in manifest['files'].items():assert len(content[name])==e['bytes'] and hashlib.sha256(content[name]).hexdigest()==e['sha256']
    return content,manifest


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();assert not a.output.exists() and load(s/'verification.json')['passed']
    files={}
    for p in sorted(s.rglob('*')):
        rel=p.relative_to(s)
        if rel.parts[0]=='restored' or not p.is_file():continue
        # A currently running monitor has no final receipt yet.
        if any(part.endswith('-monitor') for part in rel.parts) and not (s/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=p
    for directory in ('python/moe_ub_cosim','fixtures/context_transactions','examples/scaleout_context_transactions'):
        for p in sorted((repo/directory).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.json','.md') and '__pycache__' not in p.parts:files['source/'+str(p.relative_to(repo))]=p
    for rel in ('tests/unit/context_transactions_test.py','deps.lock.json','docs/tasks/context_transaction_binding_spec.md',
        'docs/architecture.md','docs/model_validation.md','docs/record_storage.md','docs/user_guide.md','docs/tasks/current.md',
        'docs/reviews/Context连接身份绑定与实包恢复验收_20261024.md'):
        files['source/'+rel]=repo/rel
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<=2048 and sum(e['bytes'] for e in entries.values())<128*2**20
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,scope='Linux x86-64/glibc ABI-bound runtime, Python source and functional evidence; no clean native build')
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data)
            info.mode=0o755 if name in ('evidence/runtime/run','evidence/runtime/transaction-probe') else 0o644
            ar.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);ar.addfile(info,io.BytesIO(data))
    admit(a.output)
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(data),compressed_bytes=a.output.stat().st_size))


def restore(a):
    assert not a.output.exists();content,manifest=admit(a.archive);a.output.mkdir()
    for name,data in content.items():
        p=a.output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    for name in ('run','transaction-probe'):(a.output/'evidence/runtime'/name).chmod(0o755)
    (a.output/'empty-cwd').mkdir()
    save(a.output/'restoration.json',dict(passed=True,members_verified=len(content),archive_sha256=sha(a.archive),
        all_hashes_verified_before_writes=True,network_executions=0,scope=manifest['scope']))


def safety(a):
    assert not a.output.exists();a.output.mkdir();rejected=[]
    for kind in ('parent','absolute','duplicate','symlink','prefix','hash'):
        name='../escape' if kind=='parent' else '/escape' if kind=='absolute' else 'evidence/file'
        entries={name:dict(bytes=1,sha256=hashlib.sha256(b'a').hexdigest())};path=a.output/(kind+'.tar.gz')
        if kind=='hash':entries[name]['sha256']='0'*64
        with tarfile.open(path,'x:gz') as ar:
            m=tarfile.TarInfo(name);m.size=1
            if kind=='symlink':m.type=tarfile.SYMTYPE;m.linkname='/outside';m.size=0
            ar.addfile(m,io.BytesIO(b'a') if m.isfile() else None)
            if kind=='duplicate':ar.addfile(m,io.BytesIO(b'a'))
            if kind=='prefix':
                child=name+'/child';m=tarfile.TarInfo(child);m.size=1;ar.addfile(m,io.BytesIO(b'a'));entries[child]=entries[name]
            data=json.dumps(dict(schema_version=SCHEMA,files=entries)).encode();m=tarfile.TarInfo('manifest.json');m.size=len(data);ar.addfile(m,io.BytesIO(data))
        try:admit(path)
        except (AssertionError,KeyError,ValueError,tarfile.TarError):rejected.append(kind)
        else:raise AssertionError('unsafe archive accepted: '+kind)
    save(a.output/'verification.json',dict(passed=True,rejected=rejected,output_files_extracted=0))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    sub.add_parser('safety').add_argument('--output',type=Path,required=True)
    a=p.parse_args();dict(create=create,restore=restore,safety=safety)[a.action](a)


if __name__=='__main__':main()
