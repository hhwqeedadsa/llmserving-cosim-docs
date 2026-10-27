"""Member-hash archive and copied-source recovery of the closed partial study.

Recovery only executes prediction, diagnosis, verification and report modules.
The retained native binaries are hashed as evidence and are never launched.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

from .data import load, save, sha

SCHEMA = 'ecmp-network-choice-checkpoint/1'
SOURCE_DIRS = (
    'python/moe_ub_cosim', 'experiments/ecmp_network_choice',
    'experiments/ecmp_baseline_audit', 'experiments/compact_network_choice',
    'experiments/shared_uplink_choice', 'experiments/static_dag_transfer',
)


def admit(archive, digest, output=None):
    assert archive.stat().st_size <= 64*2**20 and sha(archive) == digest
    files, total, manifest = {}, 0, None
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            assert member.isfile() and name.parts and not name.is_absolute()
            assert '..' not in name.parts and str(name) == member.name and '\\' not in member.name
            assert member.name not in files and len(files) < 4096
            assert 0 <= member.size <= 32*2**20 and member.mode in (0o644, 0o755)
            total += member.size
            assert total <= 192*2**20
            raw = stream.extractfile(member).read()
            assert len(raw) == member.size
            files[member.name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), mode=member.mode)
            if member.name == 'manifest.json':
                manifest = json.loads(raw)
    assert manifest is not None and manifest['schema_version'] == SCHEMA
    del files['manifest.json']
    assert files == manifest['files']
    assert all(not any(str(p) in files for p in PurePosixPath(n).parents) for n in files)
    if output is not None:
        assert not output.exists()
        output.mkdir()
        with tarfile.open(archive, 'r:gz') as stream:
            for member in stream:
                if member.name == 'manifest.json':
                    continue
                raw = stream.extractfile(member).read()
                assert hashlib.sha256(raw).hexdigest() == files[member.name]['sha256']
                destination = output/member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
                destination.chmod(files[member.name]['mode'])
    return manifest


def create(repo, run, archive):
    stage = run/'stage'
    assert load(stage/'verification-final.json')['passed'] and load(stage/'diagnosis.json')['passed']
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'failure_preserved_no_remaining_networks'
    assert not archive.exists()
    files = {}
    for path in sorted(run.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run)
        if relative.parts[0] not in ('stage', 'monitor', 'dependency-check') or 'empty-cwd' in relative.parts:
            continue
        if relative.parts[0] == 'monitor' and not (run/'monitor'/relative.parts[1]/'probe.json').exists():
            continue  # Do not copy an actively written monitor.
        files['run/'+str(relative)] = path
    for directory in SOURCE_DIRS:
        for path in sorted((repo/directory).glob('*')):
            if path.suffix in ('.py', '.md'):
                files['source/'+str(path.relative_to(repo))] = path
    files['source/deps.lock.json'] = repo/'deps.lock.json'
    for name in load(stage/'runtime-identity.json')['sources']:
        files['source/'+name] = repo/name
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path),
        mode=0o755 if os.access(path, os.X_OK) else 0o644) for name, path in sorted(files.items())}
    assert len(entries) < 4095 and sum(row['bytes'] for row in entries.values()) < 192*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert len(raw) <= 32*2**20 and hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size, info.mode = len(raw), 0o644
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive, digest, output):
    manifest = admit(archive, digest, output)
    source, stage = output/'source', output/'run/stage'
    empty = output/'empty-cwd'
    empty.mkdir()
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    records = []

    def command(module, *args):
        argv = [sys.executable, '-B', '-m', 'experiments.ecmp_network_choice.'+module, *map(str, args)]
        result = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=50)
        records.append(dict(command=argv, cwd=str(empty), exit_code=result.returncode,
                            stdout=result.stdout, stderr=result.stderr))
        save(output/'commands.json', records)
        assert result.returncode == 0, records[-1]

    command('predict', '--observed', stage/'observed', '--output', stage/'recomputed-predictions')
    for path in (stage/'predictions').iterdir():
        assert path.read_bytes() == (stage/'recomputed-predictions'/path.name).read_bytes()
    command('diagnose', '--stage', stage, '--source-root', source, '--output', stage/'recomputed-diagnosis.json')
    assert (stage/'diagnosis.json').read_bytes() == (stage/'recomputed-diagnosis.json').read_bytes()
    command('verify', '--stage', stage, '--output', stage/'recomputed-verification.json',
            '--observed-only', output/'observed-only')
    assert (stage/'verification-final.json').read_bytes() == (stage/'recomputed-verification.json').read_bytes()
    command('evaluate', 'report', '--stage', stage, '--output', stage/'recomputed-report')
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (stage/'report'/name).read_bytes() == (stage/'recomputed-report'/name).read_bytes()
    save(output/'recovery.json', dict(passed=True, archive_sha256=digest, members=len(manifest['files'])+1,
        copied_source=True, empty_cwd=True, predictions_identical=True, diagnosis_identical=True,
        verification_identical=True, reports_identical=True, original_strict_failure_preserved=True,
        native_runtime_only_hashed_not_executed=True, new_networks=0, new_builds=0))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('create', 'restore'))
    p.add_argument('--repository', type=Path)
    p.add_argument('--run', type=Path)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--sha256')
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.action == 'create':
        create(a.repository.resolve(), a.run.resolve(), a.archive.resolve())
    else:
        restore(a.archive.resolve(), a.sha256, a.output.resolve())
