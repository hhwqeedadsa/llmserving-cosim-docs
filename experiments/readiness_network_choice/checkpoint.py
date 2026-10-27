"""Bounded member-hash archive and copied-source offline recovery; no network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from experiments.readiness_network_choice.prepare import load, save, sha

SCHEMA = 'readiness-network-choice-checkpoint/1'


def admit(path, digest, output=None):
    assert path.stat().st_size <= 256*2**20 and sha(path) == digest
    actual, total, manifest = {}, 0, None
    # Check the complete archive before writing any restored member.
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            assert member.isfile() and name.parts and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in member.name and '\x00' not in member.name and str(name) == member.name
            assert member.name not in actual and len(actual) < 4096 and 0 <= member.size <= 64*2**20
            assert member.mode in (0o644, 0o755)
            total += member.size
            assert total <= 640*2**20
            raw = archive.extractfile(member).read()
            assert len(raw) == member.size
            actual[member.name] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), mode=member.mode)
            if member.name == 'manifest.json':
                manifest = json.loads(raw)
    assert manifest is not None and manifest['schema_version'] == SCHEMA
    del actual['manifest.json']
    assert actual == manifest['files']
    assert all(not any(str(p) in actual for p in PurePosixPath(n).parents) for n in actual)
    if output is not None:
        output.mkdir()
        with tarfile.open(path, 'r:gz') as archive:
            for member in archive:
                if member.name == 'manifest.json':
                    continue
                raw = archive.extractfile(member).read()
                assert hashlib.sha256(raw).hexdigest() == actual[member.name]['sha256']
                destination = output/member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
                destination.chmod(actual[member.name]['mode'])
    return manifest


def create(repo, run, archive):
    assert load(run/'stage/verification.json')['passed']
    assert load(run/'stage/execution.json')['state'] == 'completed' and not archive.exists()
    files = {}
    for path in run.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(run)
        if relative.parts[0] not in ('stage', 'monitor') or 'empty-cwd' in relative.parts:
            continue
        if relative.parts[0] == 'monitor' and not (run/'monitor'/relative.parts[1]/'probe.json').exists():
            continue
        files['run/'+str(relative)] = path
    for directory in ('python/moe_ub_cosim', 'experiments/readiness_network_choice',
                      'experiments/shared_uplink_choice', 'experiments/compact_network_choice',
                      'experiments/static_dag_transfer'):
        for path in (repo/directory).glob('*.py'):
            files['source/'+str(path.relative_to(repo))] = path
    for directory in ('experiments/readiness_network_choice', 'experiments/shared_uplink_choice'):
        for name in ('README.md', 'protocol.md'):
            files['source/'+directory+'/'+name] = repo/directory/name
    files['source/deps.lock.json'] = repo/'deps.lock.json'
    for name in load(run/'stage/runtime-identity.json')['sources']:
        files['source/'+name] = repo/name
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path), mode=0o755 if os.access(path, os.X_OK) else 0o644)
               for name, path in files.items()}
    assert len(entries) < 4096 and sum(r['bytes'] for r in entries.values()) < 640*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert len(raw) <= 64*2**20 and hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(raw), entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = load(archive.with_suffix('.receipt.json'))['sha256']
    manifest = admit(archive, digest, output)
    source, stage = output/'source', output/'run/stage'
    empty = output/'empty-cwd'
    empty.mkdir()
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    records = []

    def command(module, *args):
        argv = [sys.executable, '-B', '-m', 'experiments.readiness_network_choice.'+module, *map(str, args)]
        result = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=50)
        records.append(dict(command=argv, cwd=str(empty), exit_code=result.returncode,
                            stdout=result.stdout, stderr=result.stderr))
        save(output/'commands.json', records)
        assert result.returncode == 0, records[-1]

    command('verify', '--stage', stage, '--output', stage/'recovered-verification.json', '--scratch', output/'offline-check')
    assert (stage/'verification.json').read_bytes() == (stage/'recovered-verification.json').read_bytes()
    command('report', '--stage', stage, '--output', stage/'recovered-report')
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (stage/'report'/name).read_bytes() == (stage/'recovered-report'/name).read_bytes()
    save(output/'recovery.json', dict(passed=True, sha256=digest, members=len(manifest['files'])+1,
        copied_source=True, empty_cwd=True, identical_verification=True, identical_reports=True,
        predictions_identical_without_truth=True, new_networks=0, new_builds=0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('create', 'restore'))
    parser.add_argument('--repository', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.action == 'create':
        create(args.repository.resolve(), args.run.resolve(), args.archive.resolve())
    else:
        restore(args.archive.resolve(), args.output.resolve())
