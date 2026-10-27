"""Hash-admitted checkpoint and offline reproduction for the event-DAG study."""
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


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def create(repo, stage, output):
    assert not output.exists()
    ledger = json.loads((stage/'execution.json').read_bytes())
    assert ledger['closed'] and ledger['network_executions'] == 2
    files = {}
    for path in stage.rglob('*'):
        if path.is_file():
            assert not path.is_symlink()
            files['evidence/'+str(path.relative_to(stage))] = path
    for path in (repo/'experiments/static_dag_transfer').glob('*'):
        if path.is_file() and path.suffix in ('.py','.md'):
            files['source/'+str(path.relative_to(repo))] = path
    expected = repo/'experiments/static_dag_transfer/results/event_graph_20261027'
    for path in expected.iterdir():
        if path.is_file():
            files['expected/'+path.name] = path
    for directory in (repo/'runs/generic_event_dag_20261027').glob('*-monitor'):
        probe = directory/'probe.json'
        if not probe.is_file() or json.loads(probe.read_bytes())['status'] not in ('completed','child_failed'):
            continue
        for path in directory.iterdir():
            if path.is_file():
                files['monitor/'+str(path.relative_to(repo/'runs/generic_event_dag_20261027'))] = path
    entries = {n:dict(bytes=p.stat().st_size,sha256=sha(p)) for n,p in files.items()}
    assert len(entries) < 2048 and max(v['bytes'] for v in entries.values()) <= 16*2**20
    assert sum(v['bytes'] for v in entries.values()) < 128*2**20
    manifest = dict(schema_version='generic-event-dag-checkpoint/1', files=entries,
                    network_executions=2, hardware_calibrated=False)
    with tarfile.open(output,'x:gz',compresslevel=6) as archive:
        for name,path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
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


def restore(archive, output):
    assert not output.exists()
    expected = json.loads(archive.with_suffix('.receipt.json').read_bytes())['sha256']
    blobs = admit(archive,expected)
    output.mkdir()
    for name,raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    empty = output/'empty-cwd'
    empty.mkdir()
    command = [sys.executable,'-B','-m','experiments.static_dag_transfer.summarize_event_graph',
               '--stage',str(output/'evidence'),'--output-dir',str(output/'reanalysis')]
    env = os.environ | {'PYTHONPATH':str(output/'source'),'PYTHONDONTWRITEBYTECODE':'1'}
    done = subprocess.run(command,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
    save(output/'replay-command.json',dict(command=command,cwd=str(empty),exit_code=done.returncode,stderr=done.stderr))
    assert done.returncode == 0, done.stderr
    same = {name:(output/'reanalysis'/name).read_bytes()==blobs['expected/'+name] for name in ('summary.json','report.md')}
    assert all(same.values())
    save(output/'recovery.json',dict(passed=True,hash_verified_members=len(blobs),archive_sha256=expected,
         copied_source_root=True,empty_cwd=True,byte_identical=same,new_networks=0))


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest='action',required=True)
    c = actions.add_parser('create')
    for name in ('repository','stage','output'):
        c.add_argument('--'+name,type=Path,required=True)
    r = actions.add_parser('restore')
    for name in ('archive','output'):
        r.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    if args.action == 'create':
        create(args.repository.resolve(),args.stage.resolve(),args.output.resolve())
    else:
        restore(args.archive.resolve(),args.output.resolve())


if __name__ == '__main__':
    main()
