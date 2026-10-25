"""Admit all member hashes and paths before restoring offline source/evidence."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
from fixtures.context_transactions.checkpoint import admit,SCHEMA
from fixtures.batched_callback_replay.stage import load,save,sha


def create(repo,stage,output):
    assert load(stage/'summary.json')['verified'] and not output.exists();files={}
    for path in sorted(stage.rglob('*')):
        rel=path.relative_to(stage)
        if not path.is_file() or rel.parts[0]=='restored':continue
        if any(part.endswith('-monitor') for part in rel.parts) and not (stage/rel.parts[0]/'probe.json').exists():continue
        files['evidence/'+str(rel)]=path
    for directory in ('python/moe_ub_cosim','fixtures/batched_callback_replay'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py','.json','.md') and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repo))]=path
    for name in ('AGENTS.md','deps.lock.json','docs/tasks/current.md','docs/tasks/batched_callback_replay_spec.md',
        'docs/reviews/保留回调批次时刻的交互判别_20261025.md','tests/unit/batched_callback_replay_test.py',
        'fixtures/native_wakeup/stage.py','fixtures/context_transactions/stage.py','fixtures/context_transactions/checkpoint.py'):
        files['source/'+name]=repo/name
    entries={name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)+1<2048 and sum(row['bytes'] for row in entries.values())<48*2**20
    assert all(not path.is_symlink() and path.stat().st_size<16*2**20 for path in files.values())
    manifest=dict(schema_version=SCHEMA,files=entries,scope='offline preserved callback boundary replay only; no native binary, network or four-rank completion proof')
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in files.items():
            data=path.read_bytes();assert hashlib.sha256(data).hexdigest()==entries[name]['sha256']
            info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;archive.addfile(info,io.BytesIO(data))
        data=(json.dumps(manifest,indent=2)+'\n').encode();info=tarfile.TarInfo('manifest.json');info.size=len(data)
        archive.addfile(info,io.BytesIO(data))
    admit(output)
    save(output.with_suffix('.receipt.json'),dict(sha256=sha(output),members=len(entries)+1,
        uncompressed_bytes=sum(row['bytes'] for row in entries.values())+len(data),compressed_bytes=output.stat().st_size))


def restore(archive,output):
    assert not output.exists();content,manifest=admit(archive);output.mkdir()
    for name,data in content.items():
        path=output/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(data)
    (output/'empty-cwd').mkdir()
    save(output/'restoration.json',dict(passed=True,verified_members=len(content),archive_sha256=sha(archive),
        all_members_validated_before_writes=True,network_executed=False,scope=manifest['scope']))


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True);c=sub.add_parser('create');r=sub.add_parser('restore')
    for name in ('repository','stage','output'):c.add_argument('--'+name,type=Path,required=True)
    for name in ('archive','output'):r.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.action=='create':create(a.repository.resolve(),a.stage.resolve(),a.output.resolve())
    else:restore(a.archive.resolve(),a.output.resolve())


if __name__=='__main__':main()
