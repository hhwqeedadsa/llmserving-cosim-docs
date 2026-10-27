"""Bounded member-hashed profile evidence; restoration never measures again."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from experiments.session_admission_profile.study import load, save, sha

SCHEMA = 'session-admission-profile-checkpoint/1'


def admit(archive, expected):
    assert archive.stat().st_size <= 32*2**20 and sha(archive) == expected
    blobs, total = {}, 0
    with tarfile.open(archive, 'r:gz') as source:
        for item in source:
            name = PurePosixPath(item.name)
            assert item.isfile() and name.parts and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in item.name and '\x00' not in item.name and str(name) == item.name and item.name not in blobs
            assert len(blobs) < 2048 and 0 <= item.size <= 16*2**20
            total += item.size
            assert total <= 64*2**20
            raw = source.extractfile(item).read()
            assert len(raw) == item.size
            blobs[item.name] = raw
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == SCHEMA and set(manifest['files']) == set(blobs)
    for name, raw in blobs.items():
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents)
        assert manifest['files'][name] == dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return blobs, manifest


def create(repo, run, archive):
    assert not archive.exists() and load(run/'verification.json')['passed']
    files = {}
    for directory in ('observed', 'profiles', 'analysis'):
        for path in (run/directory).rglob('*'):
            if path.is_file() and not path.is_symlink():
                files['run/'+str(path.relative_to(run))] = path
    for directory in (run/'monitor').iterdir():
        if (directory/'probe.json').exists():
            for path in directory.iterdir():
                if path.is_file() and not path.is_symlink():
                    files['run/'+str(path.relative_to(run))] = path
    for name in ('inputs.json', 'execution.json', 'verification.json'):
        files['run/'+name] = run/name
    for directory in ('python/moe_ub_cosim', 'experiments/session_admission_profile'):
        for path in (repo/directory).glob('*'):
            if path.is_file() and path.suffix in ('.py', '.md'):
                files['source/'+str(path.relative_to(repo))] = path
    overview = 'docs/reviews/联合仿真的两个创新候选与网络收益判别_20261026.md'
    files['source/'+overview] = repo/overview
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path)) for name, path in files.items()}
    assert len(entries) < 2047 and sum(row['bytes'] for row in entries.values()) < 64*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as output:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            item = tarfile.TarInfo(name); item.size = len(raw)
            output.addfile(item, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        item = tarfile.TarInfo('manifest.json'); item.size = len(raw)
        output.addfile(item, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(row['bytes'] for row in entries.values())))


def restore(archive, output):
    assert not output.exists()
    expected = load(archive.with_suffix('.receipt.json'))['sha256']
    blobs, manifest = admit(archive, expected)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    del blobs
    source, run, empty = output/'source', output/'run', output/'empty-cwd'
    empty.mkdir()
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    records = []
    for module, args in (
        ('study', ['analyze', '--run', run, '--output', run/'reanalysis']),
        ('verify', ['--run', run, '--output', run/'reverification.json'])):
        argv = [sys.executable, '-B', '-m', 'experiments.session_admission_profile.'+module, *map(str, args)]
        result = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=45)
        records.append(dict(command=argv, cwd=str(empty), exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr))
        save(output/'commands.json', records)
        assert result.returncode == 0, records[-1]
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (run/'analysis'/name).read_bytes() == (run/'reanalysis'/name).read_bytes()
    assert (run/'verification.json').read_bytes() == (run/'reverification.json').read_bytes()
    save(output/'recovery.json', dict(passed=True, archive_sha256=expected, members=len(manifest['files'])+1,
        copied_source=True, empty_cwd=True, reports_and_verification_byte_identical=True,
        new_profiles=0, new_networks=0, new_builds=0, historical_runtime_required=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('create', 'restore'))
    for name in ('repository', 'run', 'archive', 'output'):
        parser.add_argument('--'+name, type=Path)
    args = parser.parse_args()
    if args.action == 'create': create(args.repository.resolve(), args.run.resolve(), args.archive.resolve())
    else: restore(args.archive.resolve(), args.output.resolve())
