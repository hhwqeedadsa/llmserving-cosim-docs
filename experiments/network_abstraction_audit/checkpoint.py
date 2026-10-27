"""Offline recovery of input/representation evidence, never a network runner."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile

SCHEMA = 'network-abstraction-audit-checkpoint/1'


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def admit(archive, expected):
    assert archive.stat().st_size < 32*2**20 and sha(archive) == expected
    blobs, total = {}, 0
    with tarfile.open(archive) as source:
        for member in source:
            name = PurePosixPath(member.name)
            assert member.isfile() and not name.is_absolute() and '..' not in name.parts
            assert '\\' not in member.name and str(name) == member.name and member.name not in blobs
            assert len(blobs) < 1024 and member.size <= 8*2**20
            total += member.size
            assert total < 56*2**20
            raw = source.extractfile(member).read()
            assert len(raw) == member.size
            blobs[member.name] = raw
    manifest = json.loads(blobs.pop('manifest.json'))
    assert manifest['schema_version'] == SCHEMA and set(blobs) == set(manifest['files'])
    for name, raw in blobs.items():
        assert not any(str(parent) in blobs for parent in PurePosixPath(name).parents)
        assert manifest['files'][name] == dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    return blobs


def create(repo, run, archive):
    assert not archive.exists() and read(run/'verification.json')['passed']
    files = {}
    for name in ('observed', 'analysis', 'sources', 'source', 'report', 'final-report'):
        for path in (run/name).rglob('*'):
            if path.is_file() and not path.is_symlink():
                prefix = 'report' if name == 'final-report' else 'prior-report' if name == 'report' else name
                files['run/'+prefix+'/'+str(path.relative_to(run/name))] = path
    for name in ('inputs.json', 'verification.json', 'primary-requests.json', 'final-requests.json'):
        files['run/'+name] = run/name
    for directory in (run/'monitor').iterdir():
        if (directory/'probe.json').is_file():
            for path in directory.iterdir():
                if path.is_file():
                    files['run/'+str(path.relative_to(run))] = path
    for directory in ('python/moe_ub_cosim', 'experiments/network_abstraction_audit'):
        for path in (repo/directory).rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.md'):
                files['source/'+str(path.relative_to(repo))] = path
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name,p in files.items()}
    assert len(entries) < 1023 and sum(r['bytes'] for r in entries.values()) < 56*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(raw)
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(r['bytes'] for r in entries.values())))
    print('Sealed offline representation audit.')


def restore(archive, output):
    assert not output.exists()
    expected = read(archive.with_suffix('.receipt.json'))['sha256']
    blobs = admit(archive, expected)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    del blobs
    run, source, empty = output/'run', output/'source', output/'empty-cwd'
    empty.mkdir()
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    commands = []
    def command(module, *args):
        argv = [sys.executable, '-B', '-m', 'experiments.network_abstraction_audit.'+module, *map(str,args)]
        result = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=45)
        commands.append(dict(command=argv, cwd=str(empty), exit_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr))
        save(output/'commands.json', commands)
        assert result.returncode == 0, commands[-1]
    command('study', 'analyze', '--run', run, '--output', run/'recomputed-analysis')
    old = {str(p.relative_to(run/'analysis')): p.read_bytes() for p in (run/'analysis').rglob('*') if p.is_file()}
    new = {str(p.relative_to(run/'recomputed-analysis')): p.read_bytes() for p in (run/'recomputed-analysis').rglob('*') if p.is_file()}
    assert old == new
    command('verify', '--run', run, '--analysis', run/'recomputed-analysis', '--output', run/'reverification.json')
    assert (run/'verification.json').read_bytes() == (run/'reverification.json').read_bytes()
    command('report', '--run', run, '--output', run/'recomputed-report')
    for name in ('summary.json', 'sources.json', 'verification.json', 'report.md', 'report.html'):
        assert (run/'report'/name).read_bytes() == (run/'recomputed-report'/name).read_bytes()
    save(output/'recovery.json', dict(passed=True, archive_sha256=expected, copied_source=True,
        empty_cwd=True, analysis_byte_identical=True, verification_byte_identical=True,
        reports_byte_identical=True, old_network_runtime_required=False,
        new_networks=0, new_http=0, new_builds=0, commands=commands))
    print('Recovered analysis and reports from copied sources; all bytes identical.')


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('create', 'restore'))
    for name in ('repository', 'run', 'archive', 'output'):
        p.add_argument('--'+name, type=Path)
    a = p.parse_args()
    if a.action == 'create':
        create(a.repository.resolve(), a.run.resolve(), a.archive.resolve())
    else:
        restore(a.archive.resolve(), a.output.resolve())
