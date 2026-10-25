"""Checked ABI-bound runtime/source/evidence archive, including observation failure."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit,SCHEMA
from fixtures.session_host_timing.stage import load,save,sha


def create(repo,stage,output):
    assert load(stage/'verification.json')['verified'] and not output.exists()
    output.parent.mkdir(exist_ok=True);files={}
    for path in sorted(stage.rglob('*')):
        rel=path.relative_to(stage)
        if not path.is_file() or rel.parts[0] in ('objects','restored'):continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (stage/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=path
    for directory in ('python/moe_ub_cosim','backend/rdma','fixtures/session_host_timing',
                      'fixtures/modern_native','fixtures/native_wakeup','fixtures/context_transactions'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.json','.md','.cc','.hh') and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repo))]=path
    for name in ('AGENTS.md','deps.lock.json','docs/tasks/current.md','docs/tasks/session_host_timing_spec.md',
                 'docs/architecture.md','docs/record_storage.md','backend/README.md',
                 'examples/modern_deepep_native/README.md','examples/modern_deepep_native/input.json',
                 'examples/modern_deepep_native/topology.json','tests/unit/session_host_timing_test.py',
                 'tests/unit/session_host_timing_disabled_test.cc',
                 'docs/reviews/现代事务扫描开销与网络事件密度_20261025.md'):
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)+1<2048 and sum(row['bytes'] for row in entries.values())<128*2**20
    assert all(not path.is_symlink() and path.stat().st_size<16*2**20 for path in files.values())
    import json
    manifest=dict(schema_version=SCHEMA,files=entries,
        scope='session host timing and preserved record-budget failure; same-host Linux ABI-bound eight libraries; no clean build')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in files.items():
            data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data)
            info.mode=0o755 if name in ('evidence/runtime/transaction-session','evidence/runtime/run-transaction-session') else 0o644
            archive.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data)
        archive.addfile(info,io.BytesIO(data))
    admit(output)
    save(output.with_suffix('.receipt.json'),dict(sha256=sha(output),members=len(entries)+1,
        uncompressed_bytes=sum(row['bytes'] for row in entries.values())+len(data),compressed_bytes=output.stat().st_size))


def restore(archive,output,shared):
    assert not output.exists()
    content,manifest=admit(archive);output.mkdir();linked=linked_bytes=0
    immutable={'runtime','inputs','topology','original','original-routes','default-healthy','timed-healthy','four-routes'}
    for name,data in content.items():
        path=output/name;path.parent.mkdir(exist_ok=True,parents=True)
        rel=Path(name).relative_to('evidence') if name.startswith('evidence/') else None
        source=shared/rel if shared and rel is not None else None
        if source and rel.parts[0] in immutable and source.is_file() and not source.is_symlink() and sha(source)==hashlib.sha256(data).hexdigest():
            os.link(source,path);linked+=1;linked_bytes+=len(data)
        else:path.write_bytes(data)
    for name in ('transaction-session','run-transaction-session'):(output/'evidence/runtime'/name).chmod(0o755)
    (output/'empty-cwd').mkdir()
    save(output/'restoration.json',dict(passed=True,members=len(content),all_hashes_checked_before_writes=True,
        source_files_copied=True,hardlinked_owned_immutable_files=linked,hardlinked_bytes=linked_bytes,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    r=sub.add_parser('restore')
    for name in ('archive','output','shared'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve(),a.shared.resolve())


if __name__=='__main__':main()
