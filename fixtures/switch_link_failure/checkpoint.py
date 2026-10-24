"""Bounded local evidence package, including one relocatable Linux executable.

This is a same-host replay package, not a clean native build or installation.
Restore validates paths/types/sizes/hashes before creating its output directory.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

MAX_BYTES = 84*2**20
MAX_FILE = 24*2**20


def sha(data):
    return hashlib.sha256(data).hexdigest()


def create(source, stage, output):
    files = {}
    for directory in ('python', 'backend', 'fixtures/switch_link_failure', 'examples/scaleout_link_failure'):
        for path in (source/directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.cc', '.hh', '.h', '.cmake', '.md', '.json'):
                files['source/'+path.relative_to(source).as_posix()] = path
    files['source/deps.lock.json'] = source/'deps.lock.json'
    # Preserve all command monitors and preparation failures, but only the
    # selected final functional results. Old redundant runs remain in stage.
    selected = {'queued-topology', 'topology', 'queued-failover', 'two-failures',
                'pfc-valid-buffer', 'persistent-failover', 'future-event', 'partition',
                'transaction-failover', 'baseline-old', 'baseline-final', 'audit-final',
                'rejections', 'original-native'}
    terminal_monitors = {p.parent.name for p in stage.glob('*-monitor/probe.json')}
    for path in stage.rglob('*'):
        relative = path.relative_to(stage)
        if (path.is_file() and (len(relative.parts) == 1 or relative.parts[0] in selected or
                              relative.parts[0] in terminal_monitors) and
                path.suffix in ('.json', '.jsonl', '.log', '.py', '.diff', '.cc', '.h')):
            files['evidence/'+relative.as_posix()] = path
    files['bin/probe'] = stage/'bin/probe-portable'
    for path in (stage/'lib').glob('libns3*.so'):
        files['lib/'+path.name] = path
    total = sum(p.stat().st_size for p in files.values())
    if total > MAX_BYTES or any(p.stat().st_size > MAX_FILE for p in files.values()):
        raise ValueError('checkpoint member budget exceeded before writing')
    manifest = dict(schema_version='switch-link-checkpoint/1',
                    scope='same Linux host offline audit and public packet replay; native binaries incrementally built, no clean-source-build claim',
                    bytes=total, files={n: dict(bytes=p.stat().st_size, sha256=sha(p.read_bytes())) for n, p in sorted(files.items())})
    with tarfile.open(output, 'x:gz') as archive:
        for name, path in sorted(files.items()):
            archive.add(path, arcname=name, recursive=False)
        data = (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    output.with_suffix(output.suffix+'.receipt.json').write_text(json.dumps(dict(
        archive_bytes=output.stat().st_size, archive_sha256=sha(output.read_bytes()),
        members=len(files)+1, uncompressed_bytes=total), indent=2, sort_keys=True)+'\n')


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
            if (not member.isfile() or member.name in names or member.name in ('', '.') or p.is_absolute() or '..' in p.parts or
                    '\\' in member.name or str(p) != member.name or member.size < 0 or member.size > MAX_FILE):
                raise ValueError('unsafe/duplicate/non-file checkpoint member')
            names.add(member.name)
        if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ValueError('checkpoint file/parent collision')
        manifest = json.loads(archive.extractfile('manifest.json').read())
        if names != {*manifest['files'], 'manifest.json'}:
            raise ValueError('checkpoint member set differs from manifest')
        for member in members:
            if member.name == 'manifest.json':
                continue
            data = archive.extractfile(member).read()
            identity = manifest['files'][member.name]
            if len(data) != identity['bytes'] or sha(data) != identity['sha256']:
                raise ValueError('checkpoint member hash mismatch')
        output.mkdir()
        for member in members:
            path = output/member.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.extractfile(member).read())
            path.chmod(0o755 if member.name.startswith('bin/') else 0o644)
    return dict(members_verified=len(members), bytes_verified=manifest['bytes'])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('create')
    for name in ('source', 'stage', 'output'):
        c.add_argument('--'+name, type=Path, required=True)
    r = sub.add_parser('restore')
    for name in ('archive', 'output'):
        r.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    if a.command == 'create':
        create(a.source, a.stage, a.output)
    else:
        print(json.dumps(restore(a.archive, a.output), sort_keys=True))
