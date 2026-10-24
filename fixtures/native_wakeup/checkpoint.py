"""Generic checked archive; deduplicate owned immutable evidence on restoration."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit, SCHEMA, safety
from fixtures.native_wakeup.stage import load, save, sha


def create(repo,stage,output):
    assert load(stage/'verification.json')['verified'] and not output.exists()
    output.parent.mkdir(exist_ok=True)
    files={}
    for path in sorted(stage.rglob('*')):
        relative=path.relative_to(stage)
        if relative.parts[0] in ('objects','restored') or not path.is_file():continue
        if any(part.endswith('-monitor') for part in relative.parts) and not (stage/relative.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(relative)]=path
    for directory in ('python/moe_ub_cosim','backend/rdma','fixtures/native_wakeup','fixtures/modern_native','fixtures/context_transactions'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.json','.md','.cc','.hh') and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repo))]=path
    for name in ('AGENTS.md','deps.lock.json','docs/tasks/native_wakeup_spec.md','docs/tasks/current.md',
                 'docs/architecture.md','backend/README.md','docs/reviews/原生回调唤醒与完整调用验收_20261024.md',
                 'examples/modern_deepep_native/README.md','examples/modern_deepep_native/topology.json',
                 'examples/modern_deepep_native/input.json','tests/unit/native_wakeup_test.py','tests/unit/session_wakeups_test.cc'):
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert all(not path.is_symlink() and path.stat().st_size<=16*2**20 for path in files.values())
    assert len(entries)+1<2048 and sum(row['bytes'] for row in entries.values())<128*2**20
    manifest=dict(schema_version=SCHEMA,files=entries,
        scope='native next-event wakeups including host-killed four-request failure; Linux/glibc ABI-bound eight libraries, no clean build/calibration')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in files.items():
            data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            member=tarfile.TarInfo(name);member.size=len(data)
            member.mode=0o755 if name in ('evidence/runtime/transaction-session','evidence/runtime/run-transaction-session') else 0o644
            archive.addfile(member,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();member=tarfile.TarInfo('manifest.json');member.size=len(data)
        archive.addfile(member,io.BytesIO(data))
    admit(output)
    save(output.with_suffix('.receipt.json'),dict(sha256=sha(output),members=len(entries)+1,
        uncompressed_bytes=sum(row['bytes'] for row in entries.values())+len(data),compressed_bytes=output.stat().st_size))


def restore(archive,output,shared):
    assert not output.exists()
    content,manifest=admit(archive);output.mkdir();linked=0;linked_files=0
    immutable={'runtime','original','original-routes','default-healthy','wake-healthy','wake-fault',
               'four-routes','alarms','topology','inputs','python-before-docs'}
    for name,data in content.items():
        path=output/name;path.parent.mkdir(exist_ok=True,parents=True)
        relative=Path(name).relative_to('evidence') if name.startswith('evidence/') else None
        source=shared/relative if shared is not None and relative is not None else None
        if source is not None and relative.parts[0] in immutable and source.is_file() and not source.is_symlink() and sha(source)==hashlib.sha256(data).hexdigest():
            os.link(source,path);linked+=len(data);linked_files+=1
        else:path.write_bytes(data)
    for name in ('transaction-session','run-transaction-session'):(output/'evidence/runtime'/name).chmod(0o755)
    (output/'empty-cwd').mkdir()
    save(output/'restoration.json',dict(passed=True,archive_sha256=sha(archive),all_members_verified_before_writes=True,
        verified_members=len(content),hardlinked_immutable_files=linked_files,hardlinked_immutable_bytes=linked,
        python_sources_copied=True,scope=manifest['scope']))


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    make=sub.add_parser('create')
    for name in ('repository','stage','output'):make.add_argument('--'+name,type=Path,required=True)
    extract=sub.add_parser('restore')
    for name in ('archive','output'):extract.add_argument('--'+name,type=Path,required=True)
    extract.add_argument('--shared',type=Path)
    sub.add_parser('safety').add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='create':create(args.repository.resolve(),args.stage.resolve(),args.output.resolve())
    elif args.action=='restore':restore(args.archive.resolve(),args.output.resolve(),args.shared.resolve() if args.shared else None)
    else:safety(args)


if __name__=='__main__':main()
