"""Portable bounded self-service source/runtime/evidence, with safe admission."""
import argparse
import io
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit as base_admit, safety as base_safety, SCHEMA
from fixtures.self_peer_service.stage import load, save, sha


def admit(path):
    # Same constrained regular-file-only format and pre-write hash verification.
    return base_admit(path)


def create(a):
    repo=a.repository.resolve();s=a.stage.resolve()
    assert not a.output.exists() and load(s/'verification.json')['passed']
    files={}
    for p in sorted(s.rglob('*')):
        rel=p.relative_to(s)
        if rel.parts[0] in ('restored','objects') or not p.is_file():continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (s/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=p
    for directory in ('python/moe_ub_cosim','backend/rdma','fixtures/self_peer_service',
                      'fixtures/context_transactions','examples/scaleout_self_peer','examples/scaleout_context_transactions'):
        for p in sorted((repo/directory).rglob('*')):
            if p.is_file() and p.suffix in ('.py','.cc','.hh','.json','.md') and '__pycache__' not in p.parts:
                files['source/'+str(p.relative_to(repo))]=p
    for rel in ('AGENTS.md','deps.lock.json','tests/unit/self_peer_service_test.py','docs/development.md',
                'docs/tasks/self_peer_service_spec.md','docs/user_guide.md','docs/architecture.md',
                'docs/model_validation.md','docs/record_storage.md','backend/README.md',
                'docs/reviews/同节点服务与远端通信共享内存验收_20261024.md'):
        files['source/'+rel]=repo/rel
    entries={name:dict(bytes=p.stat().st_size,sha256=sha(p)) for name,p in files.items()}
    assert len(entries)+1<2048 and sum(e['bytes'] for e in entries.values())<128*2**20
    assert all(not p.is_symlink() and p.stat().st_size<=16*2**20 for p in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,
        scope='self-peer service: ABI-bound Linux runtime, sources and evidence; excludes build objects; not a clean native build')
    import json
    with tarfile.open(a.output,'x:gz',compresslevel=6) as ar:
        for name,p in files.items():
            data=p.read_bytes();assert sha(p)==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data)
            info.mode=0o755 if name.startswith('evidence/runtime/') and '/lib/' not in name else 0o644
            ar.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);ar.addfile(info,io.BytesIO(data))
    admit(a.output)
    save(a.output.with_suffix('.receipt.json'),dict(sha256=sha(a.output),members=len(entries)+1,
        uncompressed_bytes=sum(e['bytes'] for e in entries.values())+len(data),compressed_bytes=a.output.stat().st_size))


def restore(a):
    assert not a.output.exists();content,manifest=admit(a.archive);a.output.mkdir()
    for name,data in content.items():
        p=a.output/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    for name in ('transaction-probe','transaction-session','run-transaction-probe','run-transaction-session'):
        (a.output/'evidence/runtime'/name).chmod(0o755)
    (a.output/'empty-cwd').mkdir()
    save(a.output/'restoration.json',dict(passed=True,members_verified=len(content),archive_sha256=sha(a.archive),
        all_hashes_verified_before_writes=True,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for k in ('repository','stage','output'):c.add_argument('--'+k,type=Path,required=True)
    r=sub.add_parser('restore')
    for k in ('archive','output'):r.add_argument('--'+k,type=Path,required=True)
    sub.add_parser('safety').add_argument('--output',type=Path,required=True)
    a=p.parse_args();dict(create=create,restore=restore,safety=base_safety)[a.action](a)


if __name__=='__main__':main()
