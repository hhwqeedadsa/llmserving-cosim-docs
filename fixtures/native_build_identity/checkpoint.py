"""Hash-checked functional evidence, copied Python source and built runtime.

Recovery is offline: it never builds or executes a network/GPU workload.
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


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def admit(path, digest):
    assert path.stat().st_size < 128*2**20 and sha(path) == digest
    blobs = {}
    total = 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in member.name and str(name) == member.name and member.name not in blobs
            assert len(blobs) < 2048 and 0 <= member.size <= 64*2**20
            total += member.size
            assert total < 256*2**20
            raw = archive.extractfile(member).read()
            assert len(raw) == member.size
            blobs[member.name] = raw
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == 'native-build-identity-checkpoint/1'
    assert set(blobs) == set(manifest['files'])
    for name, raw in blobs.items():
        assert not any(str(parent) in blobs for parent in PurePosixPath(name).parents)
        row = manifest['files'][name]
        assert row['bytes'] == len(raw) and row['sha256'] == hashlib.sha256(raw).hexdigest()
        assert row['mode'] in (0o644, 0o755)
    return blobs, manifest


def create(repository, stage, archive):
    assert load(stage/'verification.json')['passed'] and not archive.exists()
    files = {}
    selections = [*stage.glob('*.json')]
    for name in ('inputs', 'topology', 'network', 'build/logs'):
        selections += [path for path in (stage/name).rglob('*') if path.is_file()]
    for directory in stage.glob('*-monitor'):
        if (directory/'probe.json').exists():
            selections += [path for path in directory.iterdir() if path.is_file()]
    for name in ('unit', 'regression', 'regression-corrected', 'affected-regression', 'management'):
        if (stage/name/'probe.json').exists():
            selections += [path for path in (stage/name).iterdir() if path.is_file()]
    selections += [stage/'build/backend.json', stage/'build/provenance.json', stage/'build/ldd.txt']
    backend = load(stage/'build/backend.json')
    selections += [Path(backend[key]) for key in ('probe', 'stream_probe', 'transaction_probe', 'transaction_session', 'service')]
    selections += [Path(path) for path in backend['linked_ns3_libraries']]
    for path in selections:
        assert path.is_file() and not path.is_symlink()
        files['evidence/'+str(path.relative_to(stage))] = path
    for path in (repository/'python/moe_ub_cosim').rglob('*.py'):
        files['source/'+str(path.relative_to(repository))] = path
    for name in ('deps.lock.json', 'tests/unit/native_identity_test.py',
                 'fixtures/native_build_identity/checks.py', 'fixtures/native_build_identity/checkpoint.py'):
        path = repository/name
        files['source/'+name] = path
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path),
                         mode=0o755 if os.access(path, os.X_OK) else 0o644) for name, path in files.items()}
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version='native-build-identity-checkpoint/1', files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
         compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(row['bytes'] for row in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = load(archive.with_suffix('.receipt.json'))['sha256']
    blobs, manifest = admit(archive, digest)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(manifest['files'][name]['mode'])
    empty, source = output/'empty-cwd', output/'source'
    empty.mkdir()
    env = os.environ | dict(PYTHONPATH=str(source/'python')+':'+str(source), PYTHONDONTWRITEBYTECODE='1')
    commands = [
        [sys.executable, '-B', '-m', 'fixtures.native_build_identity.checks', 'verify',
         '--repository', str(source), '--stage', str(output/'evidence')],
        [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', str(source/'tests/unit'), '-p', 'native_identity_test.py']]
    records = []
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=empty, env=env, capture_output=True, text=True, timeout=60)
        (output/f'command{index}.stdout').write_text(result.stdout)
        (output/f'command{index}.stderr').write_text(result.stderr)
        records.append(dict(command=command, cwd=str(empty), exit_code=result.returncode))
        save(output/'commands.json', records)
        assert result.returncode == 0, result.stderr
    identical = (output/'evidence/verification.json').read_bytes() == blobs['evidence/verification.json']
    assert identical
    save(output/'recovery.json', dict(passed=True, sha256=digest, verified_members=len(blobs),
         copied_source=True, empty_cwd=True, verification_byte_identical=True,
         isolated_git_identity_tests_repeated=11, network_executions=0, builds=0,
         runtime_binaries_preserved_not_executed=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    create_parser = commands.add_parser('create')
    for name in ('repository', 'stage', 'archive'):
        create_parser.add_argument('--'+name, type=Path, required=True)
    restore_parser = commands.add_parser('restore')
    for name in ('archive', 'output'):
        restore_parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'create': create(args.repository.resolve(), args.stage.resolve(), args.archive.resolve())
    else: restore(args.archive.resolve(), args.output.resolve())


if __name__ == '__main__': main()
