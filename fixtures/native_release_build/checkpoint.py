"""Portable member-checked acceptance evidence; never executes a native program."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from fixtures.native_release_build.workflow import load, save, sha

SCHEMA = 'native-release-build-checkpoint/1'


def admit(archive, digest):
    assert archive.stat().st_size < 128*2**20 and sha(archive) == digest
    blobs = {}; total = 0
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts and '\\' not in member.name
            assert str(name) == member.name and member.name not in blobs and len(blobs) < 4096
            total += member.size
            assert 0 <= member.size <= 64*2**20 and total < 256*2**20
            blobs[member.name] = source.extractfile(member).read()
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == SCHEMA and set(blobs) == set(manifest['files'])
    for name, raw in blobs.items():
        row = manifest['files'][name]
        assert not any(str(p) in blobs for p in PurePosixPath(name).parents)
        assert row['bytes'] == len(raw) and row['sha256'] == hashlib.sha256(raw).hexdigest()
        assert row['mode'] in (0o644, 0o755)
    return blobs, manifest


def create(repo, stage, archive):
    assert load(stage/'verification.json')['passed'] and not archive.exists()
    files = {}
    for name in ('inputs', 'reference-modern', 'modern-topology', 'probe-topology', 'probe', 'modern'):
        for path in (stage/name).rglob('*'):
            if path.is_file(): files['evidence/'+str(path.relative_to(stage))] = path
    selected = list(stage.glob('*.json'))
    for name in ('backend.json', 'provenance.json', 'compiler-options.json', 'ldd.txt',
                 'cmake/CMakeCache.txt', 'cmake/compile_commands.json'):
        selected.append(stage/'build'/name)
    for root in (stage/'monitor', stage/'build/logs'):
        for directory in root.iterdir():
            if directory.is_dir() and (directory/'probe.json').exists():
                selected += [p for p in directory.iterdir() if p.is_file()]
    backend = load(stage/'build/backend.json')
    selected += [stage/'build/output/moe-rdma'/Path(backend[k]).name
        for k in ('probe', 'stream_probe', 'transaction_probe', 'transaction_session', 'service')]
    selected += [stage/'build/output/lib'/Path(n).name for n in backend['linked_ns3_libraries']]
    for path in selected:
        assert not path.is_symlink()
        files['evidence/'+str(path.relative_to(stage))] = path
    for directory in ('python/moe_ub_cosim', 'fixtures/native_release_build',
                      'fixtures/physical_nic_binding', 'fixtures/modern_native', 'fixtures/context_transactions'):
        for path in (repo/directory).glob('*.py'): files['source/'+str(path.relative_to(repo))] = path
    names = [*load(stage/'build/provenance.json')['sources'], 'deps.lock.json',
        'docs/tasks/native_release_build_spec.md', 'fixtures/native_release_build/README.md',
        'tests/unit/native_compilation_test.py', 'tests/unit/native_identity_test.py', 'tests/unit/scaleout_rdma_test.py']
    for name in names: files['source/'+name] = repo/name
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path),
        mode=0o755 if os.access(path, os.X_OK) else 0o644) for name, path in files.items()}
    assert sum(r['bytes'] for r in entries.values()) < 256*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); target.addfile(info, io.BytesIO(raw))
    digest = sha(archive); admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = load(archive.with_suffix('.receipt.json'))['sha256']
    blobs, manifest = admit(archive, digest); output.mkdir()
    for name, raw in blobs.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(manifest['files'][name]['mode'])
    del blobs
    empty = output/'empty-cwd'; empty.mkdir(); source = output/'source'
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    command = [sys.executable, '-B', '-m', 'fixtures.native_release_build.verify',
        '--stage', str(output/'evidence'), '--output', str(output/'verification.json')]
    result = subprocess.run(command, cwd=empty, env=env, capture_output=True, text=True, timeout=50)
    save(output/'command.json', dict(command=command, cwd=str(empty), exit_code=result.returncode,
        stdout=result.stdout, stderr=result.stderr))
    assert result.returncode == 0, result.stderr
    assert (output/'verification.json').read_bytes() == (output/'evidence/verification.json').read_bytes()
    save(output/'recovery.json', dict(passed=True, sha256=digest, members=len(manifest['files'])+1,
        copied_source=True, empty_cwd=True, identical_verification=True,
        historical_library_paths_required=False, new_builds=0, new_networks=0))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('create', 'restore'))
    for n in ('repository', 'stage', 'archive', 'output'): p.add_argument('--'+n, type=Path)
    a = p.parse_args()
    if a.action == 'create': create(a.repository.resolve(), a.stage.resolve(), a.archive.resolve())
    else: restore(a.archive.resolve(), a.output.resolve())
