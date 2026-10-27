"""Seal source evidence and reproduce it offline from copied research scripts."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def create(root, archive):
    assert not archive.exists()
    assert json.loads((root / 'verification.json').read_bytes())['passed']
    members = {}
    prefixes = ['cache', 'analysis', 'preserved']
    prefixes += sorted(p.name for p in root.glob('*-monitor') if (p / 'probe.json').is_file())
    for prefix in prefixes:
        for path in sorted((root / prefix).rglob('*')):
            assert not path.is_symlink()
            if path.is_file():
                members['evidence/' + path.relative_to(root).as_posix()] = path
    for name in ('selection.json', 'verification.json'):
        members['evidence/' + name] = root / name
    for path in sorted(Path(__file__).parent.iterdir()):
        if path.is_file() and path.suffix in ('.py', '.md'):
            members['source/' + path.name] = path
    manifest = {name: dict(bytes=path.stat().st_size, sha256=sha(path)) for name, path in members.items()}
    manifest_path = root / 'checkpoint-manifest.json'
    save(manifest_path, manifest)
    with tarfile.open(archive, 'w:gz') as target:
        target.add(manifest_path, arcname='manifest.json', recursive=False)
        for name, path in sorted(members.items()):
            target.add(path, arcname=name, recursive=False)
    save(archive.with_suffix('.receipt.json'), dict(sha256=sha(archive), bytes=archive.stat().st_size,
         content_members=len(members), uncompressed_content_bytes=sum(r['bytes'] for r in manifest.values())))


def restore(archive, expected, output):
    assert not output.exists() and sha(archive) == expected
    output.mkdir()
    with tarfile.open(archive, 'r:gz') as source:
        entries = source.getmembers()
        assert len(entries) < 1000 and len({r.name for r in entries}) == len(entries)
        assert sum(r.size for r in entries) < 64*2**20
        for entry in entries:
            path = PurePosixPath(entry.name)
            assert entry.isfile() and not path.is_absolute() and '..' not in path.parts
        manifest = json.load(source.extractfile('manifest.json'))
        assert set(manifest) | {'manifest.json'} == {r.name for r in entries}
        for entry in entries:
            data = source.extractfile(entry).read()
            if entry.name != 'manifest.json':
                row = manifest[entry.name]
                assert len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
            path = output / entry.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    evidence = output / 'evidence'
    source = output / 'source'
    empty = output / 'empty-cwd'
    empty.mkdir()
    commands = [
        [sys.executable, '-I', '-B', str(source / 'study.py'), '--run', str(evidence), '--output', str(evidence / 'reanalysis')],
        [sys.executable, '-I', '-B', str(source / 'verify.py'), '--run', str(evidence), '--output', str(evidence / 'reverification.json')],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=empty, capture_output=True, timeout=25)
        (output / f'command-{index}.stdout').write_bytes(result.stdout)
        (output / f'command-{index}.stderr').write_bytes(result.stderr)
        assert result.returncode == 0, (index, result.stderr.decode())
    compared = []
    for name in ('evidence.json', 'report.md', 'report.html'):
        assert sha(evidence / 'analysis' / name) == sha(evidence / 'reanalysis' / name)
        compared.append(name)
    assert sha(evidence / 'verification.json') == sha(evidence / 'reverification.json')
    save(output / 'recovery.json', dict(passed=True, archive_sha256=expected,
         content_members_verified=len(manifest), copied_source=True, empty_cwd=True,
         byte_equal_analysis=compared, byte_equal_verification=True,
         recovery_HTTP_requests=0, network_simulations=0, builds=0, GPU_runs=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('create')
    make.add_argument('--run', type=Path, required=True)
    make.add_argument('--archive', type=Path, required=True)
    recover = sub.add_parser('restore')
    recover.add_argument('--archive', type=Path, required=True)
    recover.add_argument('--sha256', required=True)
    recover.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'create':
        create(args.run.resolve(), args.archive.resolve())
    else:
        restore(args.archive.resolve(), args.sha256, args.output.resolve())
