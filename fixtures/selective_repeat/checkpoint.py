"""Package terminal selective-repeat evidence and verify/restore it offline.

Never executes a native network. Restore validates every member before writing.
The runtime binary is an incremental same-Linux-host build, not a clean build.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

MAX_BYTES = 64 * 2**20
MAX_FILE = 24 * 2**20


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def create(repository, stage, output):
    if output.exists():
        raise ValueError('archive exists')
    files = {}
    for directory in ('python', 'backend', 'fixtures/selective_repeat',
                      'examples/scaleout_rdma_streams'):
        for path in (repository / directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.cc', '.hh', '.h', '.md', '.json'):
                files['source/' + path.relative_to(repository).as_posix()] = path
    for name in ('deps.lock.json', 'docs/tasks/selective_repeat_spec.md',
                 'tests/unit/selective_repeat_test.cc', 'tests/unit/scaleout_selective_repeat_test.py',
                 'tests/unit/persistent_recovery_input_test.py'):
        files['source/' + name] = repository / name
    for path in (stage / 'portable').rglob('*'):
        if path.is_file():
            files['runtime/' + path.relative_to(stage / 'portable').as_posix()] = path
    # Only terminal monitors: never capture an actively growing samples.jsonl.
    for receipt in stage.glob('*-monitor/probe.json'):
        probe = json.loads(receipt.read_text())
        if 'ended_utc' not in probe or probe.get('remaining_live_group_pids'):
            raise ValueError('monitor is not terminal: ' + str(receipt))
        for path in receipt.parent.iterdir():
            if path.is_file():
                files['evidence/' + path.relative_to(stage).as_posix()] = path
    for path in stage.iterdir():
        if path.is_file() and path.suffix in ('.json', '.log'):
            files['evidence/' + path.name] = path
    for path in (stage / 'rejections').iterdir():
        if path.is_file():
            files['evidence/rejections/' + path.name] = path
    if any(path.is_symlink() for path in files.values()):
        raise ValueError('source symlink excluded')
    entries = {}
    total = 0
    for name, path in sorted(files.items()):
        data = path.read_bytes()
        if len(data) > MAX_FILE:
            raise ValueError('member too large')
        entries[name] = dict(bytes=len(data), sha256=sha(data))
        total += len(data)
    manifest = dict(schema_version='selective-repeat-checkpoint/1', bytes=total, files=entries,
                    scope='same-host incremental binary; fresh public network already executed; restore audits offline only')
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    if total + len(encoded) > MAX_BYTES or len(files) + 1 > 3000:
        raise ValueError('package budget exceeded')
    with tarfile.open(output, 'x:gz') as archive:
        for name, path in sorted(files.items()):
            data = path.read_bytes()
            if len(data) != entries[name]['bytes'] or sha(data) != entries[name]['sha256']:
                raise ValueError('input changed during packaging: ' + name)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.startswith('runtime/bin/') else 0o644
            archive.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo('manifest.json')
        info.size = len(encoded)
        archive.addfile(info, io.BytesIO(encoded))
    save(output.with_suffix(output.suffix + '.receipt.json'), dict(
        archive_bytes=output.stat().st_size, archive_sha256=sha(output.read_bytes()),
        members=len(files) + 1, uncompressed_bytes=total))


def restore(archive_path, output):
    if output.exists():
        raise ValueError('restore target exists')
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 3000 or sum(m.size for m in members) > MAX_BYTES:
            raise ValueError('restore aggregate budget exceeded')
        names = set()
        for member in members:
            p = PurePosixPath(member.name)
            if (not member.isfile() or member.name in names or member.name in ('', '.') or
                    p.is_absolute() or '..' in p.parts or '\\' in member.name or str(p) != member.name or
                    member.size < 0 or member.size > MAX_FILE):
                raise ValueError('unsafe/duplicate/non-file member')
            names.add(member.name)
        if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents
               if str(parent) != '.'):
            raise ValueError('file/parent collision')
        if 'manifest.json' not in names:
            raise ValueError('manifest missing')
        manifest = json.loads(archive.extractfile('manifest.json').read())
        if manifest.get('schema_version') != 'selective-repeat-checkpoint/1':
            raise ValueError('unsupported manifest')
        if names != {*manifest['files'], 'manifest.json'}:
            raise ValueError('member set differs from manifest')
        total = 0
        for member in members:
            if member.name == 'manifest.json':
                continue
            data = archive.extractfile(member).read()
            identity = manifest['files'][member.name]
            if len(data) != identity['bytes'] or sha(data) != identity['sha256']:
                raise ValueError('member hash mismatch')
            total += len(data)
        if total != manifest['bytes']:
            raise ValueError('manifest size mismatch')
        output.mkdir()
        for member in members:
            path = output / member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(0o755 if member.name.startswith('runtime/bin/') else 0o644)
    return dict(passed=True, members_verified=len(members), bytes_verified=total,
                network_executed=False)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create')
    for name in ('repository', 'stage', 'output'):
        c.add_argument('--' + name, type=Path, required=True)
    r = sub.add_parser('restore')
    for name in ('archive', 'output'):
        r.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    if a.command == 'create':
        create(a.repository, a.stage, a.output)
    else:
        print(json.dumps(restore(a.archive, a.output), sort_keys=True))
