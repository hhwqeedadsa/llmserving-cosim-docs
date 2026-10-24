"""Bounded research checkpoint and isolated offline replay, no network access."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

LOCAL = Path(__file__).resolve().parent
ROOT = LOCAL.parents[1]
MAX_BYTES = 16 * 2**20
SCHEMA = 'window-lifecycle-checkpoint/1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def pack(stage, output):
    files = {}
    for path in sorted(stage.rglob('*')):
        if path.is_symlink():
            raise ValueError('stage symlink')
        if path.is_file() and path.name != 'checkpoint.tar.gz' and not path.is_relative_to(stage / 'pack-monitor'):
            files['evidence/' + str(path.relative_to(stage))] = path.read_bytes()
    for path in LOCAL.iterdir():
        if path.is_file() and not path.is_symlink():
            files['source/experiments/window_lifecycle/' + path.name] = path.read_bytes()
    for name in ('experiments/target_retirement_contract/extract.py', 'docs/reviews/窗口注销合法边界与底层注册复用_20261024.md'):
        files['source/' + name] = (ROOT / name).read_bytes()
    manifest = dict(schema_version=SCHEMA, files={name: dict(bytes=len(raw), sha256=sha(raw)) for name, raw in sorted(files.items())})
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    if len(files) > 512 or sum(map(len, files.values())) > MAX_BYTES:
        raise ValueError('archive member/byte limit')
    with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name);member.mode = 0o644;member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    receipt = dict(schema_version=SCHEMA, members=len(files), plain_bytes=sum(map(len, files.values())),
                   archive_bytes=output.stat().st_size, archive_sha256=sha(output.read_bytes()))
    print(json.dumps(receipt))


def restore(archive, output):
    if output.exists() or archive.stat().st_size > MAX_BYTES:
        raise ValueError('existing output or oversized archive')
    files = {};total = 0
    with tarfile.open(archive, 'r:gz') as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            if member.name != 'manifest.json' and (path.is_absolute() or '..' in path.parts or
                str(path) != member.name or len(path.parts) < 2 or path.parts[0] not in ('source', 'evidence')):
                raise ValueError('unsafe member path')
            if not member.isfile() or member.name in files or member.size < 0:
                raise ValueError('duplicate/nonregular member')
            total += member.size
            if total > MAX_BYTES or len(files) >= 512:
                raise ValueError('archive expansion limit')
            files[member.name] = stream.extractfile(member).read()
    manifest = json.loads(files.pop('manifest.json'))
    if manifest['schema_version'] != SCHEMA or set(files) != set(manifest['files']):
        raise ValueError('manifest mismatch')
    for name, raw in files.items():
        if manifest['files'][name] != dict(bytes=len(raw), sha256=sha(raw)):
            raise ValueError('member hash mismatch')
    output.mkdir()
    for name, raw in files.items():
        path = output / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(raw)
    print(json.dumps(dict(restored=len(files), all_member_hashes_verified=True, executes_on_restore=False)))


def replay(stage, output):
    output.mkdir();cwd = output / 'empty';cwd.mkdir()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1');env.pop('PYTHONPATH', None)
    commands = []
    def run(name, command):
        command = list(map(str, command))
        with (output / (name + '.stdout')).open('xb') as stdout, (output / (name + '.stderr')).open('xb') as stderr:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=15)
        commands.append(dict(command=command, cwd=str(cwd), exit_code=result.returncode));save(output / 'commands.json', commands)
        if result.returncode:
            raise RuntimeError('replay failed, preserved without retry: ' + name)
    script = [sys.executable, '-B', LOCAL / 'audit.py']
    run('build', script + ['build', '--inputs', stage / 'inputs', '--output', output / 'build'])
    run('records', [output / 'build/source-harness'])
    run('evidence', script + ['evidence', '--inputs', stage / 'inputs', '--output', output / 'evidence.json'])
    run('analyze', script + ['analyze', '--records', output / 'records.stdout', '--output', output / 'summary.json'])
    run('verify', [sys.executable, '-B', LOCAL / 'verify.py', '--inputs', stage / 'inputs', '--build', output / 'build',
                   '--records', output / 'records.stdout', '--summary', output / 'summary.json',
                   '--evidence', output / 'evidence.json', '--output', output / 'verification.json'])
    equal = {}
    for original, generated in [('build/selected.cc', 'build/selected.cc'), ('source-run-monitor/stdout.log', 'records.stdout'),
                                ('evidence.json', 'evidence.json'), ('summary.json', 'summary.json'), ('verification.json', 'verification.json')]:
        raw = (output / generated).read_bytes()
        if raw != (stage / original).read_bytes():
            raise AssertionError('byte mismatch: ' + generated)
        equal[generated] = sha(raw)
    receipt = dict(schema_version='window-lifecycle-replay/1', byte_equal=equal, commands=len(commands),
                   real_provider_executed=False, new_ns3_networks=0, same_linux_host_only=True)
    save(output / 'replay.json', receipt);print(json.dumps(receipt))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pack', 'restore', 'replay'))
    parser.add_argument('--stage', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.stage.resolve(), args.output.resolve())
    elif args.action == 'restore':
        restore(args.archive.resolve(), args.output.resolve())
    else:
        replay(args.stage.resolve(), args.output.resolve())
