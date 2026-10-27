"""Preserve public capture evidence and reproduce the audit without a network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from experiments.static_dag_transfer.prepare import admit


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create(repo,stage,output):
    assert not output.exists()
    assert json.loads((stage/'verification.json').read_bytes())['passed']
    files = {}
    for path in stage.rglob('*'):
        if path.is_file():
            assert not path.is_symlink()
            files['evidence/'+str(path.relative_to(stage))] = path
    for path in (repo/'experiments/interaction_scope').glob('*'):
        if path.is_file() and path.suffix in ('.py','.md'):
            files['source/'+str(path.relative_to(repo))] = path
    common = repo/'experiments/static_dag_transfer/prepare.py'
    files['source/'+str(common.relative_to(repo))] = common
    for path in (repo/'experiments/interaction_scope/results/20261027').iterdir():
        if path.is_file():
            files['expected/'+path.name] = path
    for directory in (repo/'runs/interaction_scope_20261027').glob('*-monitor'):
        probe = directory/'probe.json'
        if not probe.is_file() or json.loads(probe.read_bytes())['status']!='completed':
            continue
        for path in directory.iterdir():
            if path.is_file():
                files['monitor/'+str(path.relative_to(repo/'runs/interaction_scope_20261027'))] = path
    entries = {name:dict(bytes=path.stat().st_size,sha256=sha(path)) for name,path in files.items()}
    assert len(entries)<2048 and max(v['bytes'] for v in entries.values())<=16*2**20
    assert sum(v['bytes'] for v in entries.values())<128*2**20
    manifest = dict(schema_version='interaction-scope-checkpoint/1',files=entries,new_networks=0)
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest()==entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o644
            archive.addfile(info,io.BytesIO(raw))
        raw = (json.dumps(manifest,indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        archive.addfile(info,io.BytesIO(raw))
    digest = sha(output)
    admit(output,digest)
    save(output.with_suffix('.receipt.json'),dict(sha256=digest,members=len(entries)+1,
        compressed_bytes=output.stat().st_size,uncompressed_bytes=sum(v['bytes'] for v in entries.values())))


def restore(archive,output):
    assert not output.exists()
    digest = json.loads(archive.with_suffix('.receipt.json').read_bytes())['sha256']
    blobs = admit(archive,digest)
    output.mkdir()
    for name,raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    empty = output/'empty-cwd'
    empty.mkdir()
    command = [sys.executable,'-B','-m','experiments.interaction_scope.summarize',
               '--stage',str(output/'evidence'),'--output-dir',str(output/'reanalysis')]
    env = os.environ | {'PYTHONPATH':str(output/'source'),'PYTHONDONTWRITEBYTECODE':'1'}
    done = subprocess.run(command,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
    save(output/'replay-command.json',dict(command=command,cwd=str(empty),exit_code=done.returncode,stderr=done.stderr))
    assert done.returncode==0,done.stderr
    same = {name:(output/'reanalysis'/name).read_bytes()==blobs['expected/'+name] for name in ('summary.json','report.md')}
    assert all(same.values())
    save(output/'recovery.json',dict(passed=True,hash_verified_members=len(blobs),archive_sha256=digest,
        copied_source_root=True,empty_cwd=True,byte_identical=same,new_networks=0))


def main():
    parser=argparse.ArgumentParser()
    actions=parser.add_subparsers(dest='action',required=True)
    c=actions.add_parser('create')
    for name in ('repository','stage','output'):
        c.add_argument('--'+name,type=Path,required=True)
    r=actions.add_parser('restore')
    for name in ('archive','output'):
        r.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.action=='create':
        create(args.repository.resolve(),args.stage.resolve(),args.output.resolve())
    else:
        restore(args.archive.resolve(),args.output.resolve())


if __name__=='__main__':
    main()
