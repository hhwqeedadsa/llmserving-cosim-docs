"""Source/runtime/evidence snapshot admitted before any restoration writes."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit,SCHEMA
from fixtures.session_host_timing.checkpoint import restore
from fixtures.incremental_progress.stage import load,save,sha


def create(repo,stage,output):
    assert load(stage/'verification.json')['verified'] and not output.exists();files={}
    for path in sorted(stage.rglob('*')):
        rel=path.relative_to(stage)
        if not path.is_file() or rel.parts[0] in ('objects','restored'):continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (stage/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=path
    for directory in ('python/moe_ub_cosim','backend/rdma','fixtures/incremental_progress',
        'fixtures/session_host_timing','fixtures/modern_native','fixtures/native_wakeup','fixtures/context_transactions'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.json','.md','.cc','.hh') and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repo))]=path
    for name in ('AGENTS.md','deps.lock.json','docs/tasks/current.md','docs/tasks/incremental_progress_spec.md',
        'docs/architecture.md','docs/record_storage.md','backend/README.md','examples/modern_deepep_native/README.md',
        'examples/modern_deepep_native/input.json','examples/modern_deepep_native/topology.json',
        'docs/reviews/增量收集与四卡控制交互边界_20261025.md'):
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)+1<2048 and sum(row['bytes'] for row in entries.values())<128*2**20
    assert all(not path.is_symlink() and path.stat().st_size<16*2**20 for path in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,scope='incremental state collection, preserved 256-decision failure; same-host ABI-bound runtime/eight libraries; no clean native build')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in files.items():
            data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data)
            info.mode=0o755 if name.startswith('evidence/runtime/') and not '/lib/' in name else 0o644
            archive.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data)
        archive.addfile(info,io.BytesIO(data))
    admit(output)
    save(output.with_suffix('.receipt.json'),dict(sha256=sha(output),members=len(entries)+1,
        uncompressed_bytes=sum(row['bytes'] for row in entries.values())+len(data),compressed_bytes=output.stat().st_size))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True);c=sub.add_parser('create');r=sub.add_parser('restore')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    for name in ('archive','output','shared'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve(),a.shared.resolve())


if __name__=='__main__':main()
