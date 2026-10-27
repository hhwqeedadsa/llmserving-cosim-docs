"""Hash-bound source recovery of host evidence; no backend execution."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from experiments.shared_uplink_choice.prepare import admit, load, save, sha

SCHEMA = 'shared-host-cost-checkpoint/1'


def create(repo, run, archive):
    assert load(run/'stage/verification.json')['passed']
    assert load(run/'stage/execution.json')['closed'] and not archive.exists()
    files = {}
    for path in run.rglob('*'):
        if not path.is_file() or path.is_symlink(): continue
        relative = path.relative_to(run)
        if relative.parts[0] not in ('stage', 'monitor') or 'empty-cwd' in relative.parts: continue
        if relative.parts[0] == 'monitor' and not (run/'monitor'/relative.parts[1]/'probe.json').exists(): continue
        files['run/'+str(relative)] = path
    for name in ('python/moe_ub_cosim', 'experiments/shared_host_cost',
                 'experiments/shared_uplink_choice', 'experiments/static_dag_transfer',
                 'experiments/compact_network_choice'):
        for path in (repo/name).glob('*.py'): files['source/'+str(path.relative_to(repo))] = path
    for name in load(run/'stage/runtime-identity.json')['sources']:
        files['source/'+name] = repo/name
    for name in ('deps.lock.json', 'experiments/shared_host_cost/protocol.md',
                 'experiments/shared_host_cost/README.md'):
        files['source/'+name] = repo/name
    entries = {name: dict(bytes=path.stat().st_size, sha256=sha(path),
        mode=0o755 if os.access(path, os.X_OK) else 0o644) for name, path in files.items()}
    assert sum(r['bytes'] for r in entries.values()) < 256*2**20
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(files.items()):
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw)
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive)
    admit(archive, digest, SCHEMA)
    save(archive.with_suffix('.receipt.json'), dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(r['bytes'] for r in entries.values())))


def restore(archive, output):
    assert not output.exists()
    digest = load(archive.with_suffix('.receipt.json'))['sha256']
    blobs, manifest = admit(archive, digest, SCHEMA)
    output.mkdir()
    for name, raw in blobs.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw); path.chmod(manifest['files'][name]['mode'])
    del blobs
    source, empty, stage = output/'source', output/'empty-cwd', output/'run/stage'
    empty.mkdir()
    for name, sha256 in load(stage/'runtime-identity.json')['sources'].items(): assert sha(source/name) == sha256
    env = os.environ | {'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    commands = []
    def command(module, *args):
        argv = [sys.executable, '-B', '-m', 'experiments.shared_host_cost.'+module, *map(str, args)]
        result = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=50)
        commands.append(dict(command=argv, cwd=str(empty), exit_code=result.returncode,
            stdout=result.stdout, stderr=result.stderr))
        assert result.returncode == 0, commands[-1]
    command('verify', '--stage', stage, '--output', stage/'recovery-verification.json')
    assert (stage/'verification.json').read_bytes() == (stage/'recovery-verification.json').read_bytes()
    command('analyze', 'report', '--stage', stage, '--output', stage/'recovered-report')
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (stage/'report'/name).read_bytes() == (stage/'recovered-report'/name).read_bytes()
    save(output/'recovery.json', dict(passed=True, sha256=digest, members=len(manifest['files'])+1,
        copied_source=True, empty_cwd=True, identical_verification=True, identical_reports=True,
        historical_library_paths_required=False, new_builds=0, new_networks=0, commands=commands))


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('create', 'restore'))
    for n in ('repository', 'run', 'archive', 'output'): p.add_argument('--'+n, type=Path)
    a = p.parse_args()
    if a.action == 'create': create(a.repository.resolve(), a.run.resolve(), a.archive.resolve())
    else: restore(a.archive.resolve(), a.output.resolve())
