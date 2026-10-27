"""Preserve the busy-prefix evidence and reproduce without a native backend."""
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
from experiments.interaction_scope.busy_study import save


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create(repo, stage, output):
    assert not output.exists() and json.loads((stage/'verification.json').read_bytes())['passed']
    files = {'evidence/'+str(p.relative_to(stage)): p for p in stage.rglob('*') if p.is_file()}
    assert all(not p.is_symlink() for p in files.values())
    for path in (repo/'experiments/interaction_scope').glob('busy_*'):
        if path.is_file() and path.suffix in ('.py', '.md'):
            files['source/'+str(path.relative_to(repo))] = path
    path = repo/'experiments/static_dag_transfer/prepare.py'
    files['source/'+str(path.relative_to(repo))] = path
    for path in (repo/'experiments/interaction_scope/results/busy_prefix_20261027').iterdir():
        if path.is_file():
            files['expected/'+path.name] = path
    for directory in (repo/'runs/busy_prefix_20261027').glob('*-monitor'):
        probe = directory/'probe.json'
        if probe.is_file() and json.loads(probe.read_bytes())['status'] == 'completed':
            files.update({'monitor/'+str(p.relative_to(repo/'runs/busy_prefix_20261027')): p
                          for p in directory.iterdir() if p.is_file()})
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name, p in files.items()}
    assert len(entries) < 2048 and max(v['bytes'] for v in entries.values()) <= 16*2**20
    assert sum(v['bytes'] for v in entries.values()) < 128*2**20
    with tarfile.open(output, 'x:gz', compresslevel=6) as archive:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), 0o644
            archive.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version='busy-prefix-checkpoint/1', files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    digest = sha(output)
    admit(output, digest)
    save(output.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=output.stat().st_size, uncompressed_bytes=sum(v['bytes'] for v in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = json.loads(archive.with_suffix('.receipt.json').read_bytes())['sha256']
    blobs = admit(archive, digest)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    empty = output/'empty-cwd'
    empty.mkdir()
    env = os.environ | {'PYTHONPATH': str(output/'source'), 'PYTHONDONTWRITEBYTECODE': '1'}
    # Create an isolated observed-only input for the regenerated plans/snapshots.
    observed_stage = output/'regenerated'
    observed_stage.mkdir()
    import shutil
    shutil.copytree(output/'evidence/observed', observed_stage/'observed')
    commands = [
        [sys.executable, '-B', '-m', 'experiments.interaction_scope.busy_study', 'predict',
         '--observed', str(observed_stage/'observed'), '--output', str(observed_stage/'predictions')],
        [sys.executable, '-B', '-m', 'experiments.interaction_scope.busy_study', 'snapshots', '--stage', str(observed_stage)],
        [sys.executable, '-B', '-m', 'experiments.interaction_scope.busy_study', 'report',
         '--stage', str(output/'evidence'), '--output', str(output/'reanalysis')],
    ]
    records = []
    for command in commands:
        done = subprocess.run(command, cwd=empty, env=env, capture_output=True, text=True, timeout=60)
        records.append(dict(command=command, cwd=str(empty), exit_code=done.returncode, stderr=done.stderr))
        save(output/'commands.json', records)
        assert done.returncode == 0, done.stderr
    predictions = {p.name: p.read_bytes() == blobs['evidence/predictions/'+p.name]
                   for p in (observed_stage/'predictions').iterdir()}
    same = {name: (output/'reanalysis'/name).read_bytes() == blobs['expected/'+name] for name in ('summary.json', 'report.md')}
    assert len(predictions) == 4 and all(predictions.values()) and all(same.values())
    save(output/'recovery.json', dict(passed=True, archive_sha256=digest, hash_verified_members=len(blobs),
        copied_source=True, empty_cwd=True, byte_identical=same, prediction_byte_identical=predictions, new_networks=0))


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest='action', required=True)
    p = actions.add_parser('create')
    for name in ('repository', 'stage', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p = actions.add_parser('restore')
    for name in ('archive', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'create':
        create(args.repository.resolve(), args.stage.resolve(), args.output.resolve())
    else:
        restore(args.archive.resolve(), args.output.resolve())


if __name__ == '__main__':
    main()
