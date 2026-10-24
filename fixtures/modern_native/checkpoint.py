"""Bounded source/data/runtime snapshot; safety checks precede all writes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit, safety, SCHEMA
from fixtures.modern_native.stage import load,save,sha


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve();assert load(s/'verification.json')['verified'] and not a.output.exists()
    files={}
    for p in sorted(s.rglob('*')):
        rel=p.relative_to(s)
        if rel.parts[0]=='restored' or not p.is_file():continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (s/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=p
    for directory in ('python/moe_ub_cosim','backend/rdma','fixtures/modern_native','fixtures/context_transactions',
                      'examples/modern_deepep_native','examples/modern_deepep_partial_order'):
        for p in sorted((repo/directory).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.cc','.hh','.json','.md') and '__pycache__' not in p.parts:
                files['source/'+str(p.relative_to(repo))]=p
    for rel in ('AGENTS.md','deps.lock.json','tests/unit/modern_native_test.py','docs/tasks/modern_native_bridge_spec.md',
                'docs/architecture.md','docs/model_validation.md','docs/record_storage.md','docs/user_guide.md',
                'docs/reviews/现代完整操作与真实网络回调接入_20261024.md'):
        files['source/'+rel]=repo/rel
    for row in load(s/'route-provenance.json')['original_files']:
        p=repo/row['path'];assert sha(p)==row['sha256'];files['source-data/'+p.name]=p
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<2048 and sum(e['bytes'] for e in entries.values())<128*2**20
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,scope='modern native functional integration, including required failed four-rank case; same-host ABI runtime, not clean build/calibration')
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o755 if name in ('evidence/runtime/transaction-session','evidence/runtime/run-transaction-session') else 0o644
            ar.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);ar.addfile(info,io.BytesIO(data))
    admit(a.output)
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(data),compressed_bytes=a.output.stat().st_size))


def restore(a):
    assert not a.output.exists();content,manifest=admit(a.archive);a.output.mkdir()
    for name,data in content.items():
        p=a.output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    for name in ('transaction-session','run-transaction-session'):(a.output/'evidence/runtime'/name).chmod(0o755)
    (a.output/'empty-cwd').mkdir()
    save(a.output/'restoration.json',dict(passed=True,members_verified=len(content),archive_sha256=sha(a.archive),all_hashes_verified_before_writes=True,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for key in ('repository','stage','output'):c.add_argument('--'+key,type=Path,required=True)
    r=sub.add_parser('restore')
    for key in ('archive','output'):r.add_argument('--'+key,type=Path,required=True)
    sub.add_parser('safety').add_argument('--output',type=Path,required=True)
    a=p.parse_args();dict(create=create,restore=restore,safety=safety)[a.action](a)


if __name__=='__main__':main()
