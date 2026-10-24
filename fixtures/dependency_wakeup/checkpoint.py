"""Small offline-only checkpoint; validate every member before any extraction."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

SCHEMA = 'moe-dependency-wakeup-checkpoint/1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def admit(path):
    assert path.stat().st_size <= 8*2**20
    content, total = {}, 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            assert len(content) < 256 and member.isfile() and not name.is_absolute()
            assert '..' not in name.parts and str(name) == member.name and '\\' not in member.name
            assert member.name not in content and member.name not in ('', '.')
            assert 0 <= member.size <= 4*2**20
            total += member.size
            assert total <= 16*2**20
            content[member.name] = archive.extractfile(member).read()
            assert len(content[member.name]) == member.size
    assert not any(str(parent) in content for name in content
                   for parent in PurePosixPath(name).parents if str(parent) != '.')
    manifest = json.loads(content['manifest.json'])
    assert manifest['schema_version'] == SCHEMA
    assert set(manifest['files']) == content.keys()-{'manifest.json'}
    for name, identity in manifest['files'].items():
        assert len(content[name]) == identity['bytes'] and sha(content[name]) == identity['sha256']
    return content, manifest


def create(repo, stage, source, output):
    assert not output.exists() and json.loads((stage/'verification.json').read_text())['verified']
    files = {}
    for directory in ('python/moe_ub_cosim', 'fixtures/dependency_wakeup'):
        for path in sorted((repo/directory).rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.md') and '__pycache__' not in path.parts:
                files['source/'+str(path.relative_to(repo))] = path
    for name in ('AGENTS.md', 'docs/tasks/current.md', 'docs/tasks/dependency_wakeup_spec.md',
                 'docs/architecture.md', 'docs/reviews/依赖唤醒离线判别_20261024.md',
                 'examples/modern_deepep_native/input.json', 'examples/modern_deepep_partial_order/input.json',
                 'tests/unit/dependency_wakeup_test.py'):
        files['source/'+name] = repo/name
    for case in ('synthetic-healthy', 'synthetic-fault', 'four-routes'):
        for name in ('topology.json', 'protocol.json', 'input.json', 'control.jsonl', 'modern-execution.json'):
            files['original/'+case+'/'+name] = source/case/name
    for name in ('audit.json', 'audit-evidence.json', 'verification.json', 'commands.json'):
        files['evidence/'+name] = stage/name
    if (stage/'recovery.json').is_file():
        files['evidence/recovery.json'] = stage/'recovery.json'
    for path in sorted(stage.glob('*-monitor/*')):
        if path.is_file() and (path.parent/'probe.json').is_file():
            files['evidence/'+str(path.relative_to(stage))] = path
    assert all(not path.is_symlink() for path in files.values())
    identities = {name: dict(bytes=path.stat().st_size, sha256=sha(path.read_bytes())) for name, path in files.items()}
    manifest = dict(schema_version=SCHEMA, files=identities,
                    scope='offline replay of existing callbacks only; no native runtime/network or four-request completion proof')
    with tarfile.open(output, 'x:gz', compresslevel=6) as archive:
        for name, path in files.items():
            data = path.read_bytes()
            assert sha(data) == identities[name]['sha256']
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        data = (json.dumps(manifest, indent=2)+'\n').encode()
        member = tarfile.TarInfo('manifest.json')
        member.size = len(data)
        archive.addfile(member, io.BytesIO(data))
    content, _ = admit(output)
    receipt = dict(sha256=sha(output.read_bytes()), members=len(content),
                   uncompressed_bytes=sum(map(len, content.values())), compressed_bytes=output.stat().st_size)
    output.with_suffix('.receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')


def restore(archive, output):
    assert not output.exists()
    content, manifest = admit(archive)
    output.mkdir()
    for name, data in content.items():
        path = output/name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(data)
    (output/'empty-cwd').mkdir()
    (output/'restoration.json').write_text(json.dumps(dict(verified_members=len(content),
        archive_sha256=sha(archive.read_bytes()), all_members_validated_before_writes=True,
        scope=manifest['scope']), indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    make = sub.add_parser('create')
    for name in ('repository', 'stage', 'source', 'output'):
        make.add_argument('--'+name, type=Path, required=True)
    extract = sub.add_parser('restore')
    for name in ('archive', 'output'):
        extract.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'create':
        create(args.repository, args.stage, args.source, args.output)
    else:
        restore(args.archive, args.output)


if __name__ == '__main__':
    main()
