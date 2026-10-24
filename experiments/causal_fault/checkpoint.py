"""Verified source/evidence archive and offline replay; never starts a network."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile


def load(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')


def create(a):
    repo = a.repository.resolve(); stage = a.stage.resolve(); files = {}
    assert not a.output.exists() and load(stage/'run.json')['actual_networks'] == 4
    assert load(stage/'verification-final.json')['passed']
    for directory in ('inputs', 'planned', 'topology', 'network'):
        for p in sorted((stage/directory).rglob('*')):
            if p.is_file(): files[str(p.relative_to(stage))] = p
    for name in ('analysis.json', 'mechanism.json', 'verification.json', 'verification-strong.json', 'verification-final.json',
                 'runtime.json', 'commands.json', 'run.json', 'analysis-failure.json', 'failed-verifier-v1.py', 'failed-verifier-v2.py'):
        files[name] = stage/name
    for p in sorted(stage.glob('*-monitor/*')):
        if p.is_file() and (p.parent/'probe.json').is_file(): files['monitors/'+str(p.relative_to(stage))] = p
    for p in sorted((repo/'experiments/causal_fault').glob('*')):
        if p.is_file(): files['source/experiments/causal_fault/'+p.name] = p
    report = 'docs/reviews/应用依赖下的故障影响与无反馈等待_20261024.md'
    files['source/'+report] = repo/report
    # Original local routes remain ignored/private run artifacts, never Git data.
    manifest = load(repo/'data/moe-trace-small/manifest.json')
    files['source/data/moe-trace-small/manifest.json'] = repo/'data/moe-trace-small/manifest.json'
    for entry in manifest['files']:
        name = entry['file']['path']; files['source/'+name] = repo/name
    assert all(not p.is_symlink() and p.is_file() and p.stat().st_size <= 16*2**20 for p in files.values())
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name, p in files.items()}
    assert len(files)+1 <= 2048 and sum(v['bytes'] for v in entries.values()) < 96*2**20
    manifest = dict(schema_version='moe-causal-fault-checkpoint/1', files=entries,
        scope='four saved network results and standalone standard-library offline source replay; no native runtime/clean build/modern fidelity')
    with tarfile.open(a.output, 'x:gz', compresslevel=6) as archive:
        for name, p in files.items():
            data = p.read_bytes(); assert len(data) == entries[name]['bytes'] and hashlib.sha256(data).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        data = (json.dumps(manifest, indent=2)+'\n').encode(); info = tarfile.TarInfo('manifest.json'); info.size = len(data); archive.addfile(info, io.BytesIO(data))
    assert a.output.stat().st_size < 24*2**20
    save(a.output.with_suffix('.receipt.json'), dict(sha256=sha(a.output), compressed_bytes=a.output.stat().st_size,
        members=len(files)+1, uncompressed_bytes=sum(v['bytes'] for v in entries.values())+len(data), scope=manifest['scope']))


def restore(a):
    assert not a.output.exists()
    with tarfile.open(a.archive, 'r:gz') as archive:
        members = archive.getmembers(); assert len(members) <= 2048
        names = set(); total = 0
        for m in members:
            path = PurePosixPath(m.name)
            assert m.isfile() and m.name not in ('', '.') and not path.is_absolute() and '..' not in path.parts
            assert str(path) == m.name and '\\' not in m.name and m.name not in names and 0 <= m.size <= 16*2**20
            names.add(m.name); total += m.size
        assert total < 96*2**20 and 'manifest.json' in names
        assert not any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent) != '.')
        manifest = json.load(archive.extractfile('manifest.json'))
        assert manifest['schema_version'] == 'moe-causal-fault-checkpoint/1' and set(manifest['files']) == names-{'manifest.json'}
        for m in members:
            if m.name == 'manifest.json': continue
            data = archive.extractfile(m).read(); entry = manifest['files'][m.name]
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
        a.output.mkdir()
        for m in members:
            p = a.output/m.name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(archive.extractfile(m).read())
    root = a.output.resolve(); source = root/'source/experiments/causal_fault'; cwd = root/'empty-cwd'; cwd.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONDONTWRITEBYTECODE='1'); commands = []
    commands_to_run = [
        ('analysis', [source/'analyze.py', '--observed', root/'inputs/observed', '--planned', root/'planned', '--network', root/'network', '--output', root/'replayed-analysis.json']),
        ('mechanism', [source/'mechanism.py', '--network', root/'network', '--analysis', root/'replayed-analysis.json', '--output', root/'replayed-mechanism.json']),
        ('verification', [source/'verify.py', '--repository', root/'source', '--stage', root, '--analysis', root/'replayed-analysis.json',
            '--mechanism', root/'replayed-mechanism.json', '--output', root/'replayed-verification.json'])]
    for label, argv in commands_to_run:
        cmd = [sys.executable, '-B', *map(str, argv)]
        with (root/(label+'.stdout.log')).open('w') as stdout, (root/(label+'.stderr.log')).open('w') as stderr:
            r = subprocess.run(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=60)
        commands.append(dict(command=cmd, cwd=str(cwd), pythonpath='', exit_code=r.returncode)); save(root/'replay-commands.json', commands)
        assert r.returncode == 0, 'offline failure: '+label
    identical = {}
    for name, original in (('analysis', 'analysis.json'), ('mechanism', 'mechanism.json'), ('verification', 'verification-final.json')):
        identical[name] = (root/original).read_bytes() == (root/('replayed-'+name+'.json')).read_bytes()
    assert all(identical.values())
    save(root/'replay.json', dict(passed=True, members_verified=len(members), archive_sha256=sha(a.archive),
        byte_identical=identical, new_source_root=True, empty_cwd=True, empty_pythonpath=True, network_executions=0, commands=commands,
        scope='offline verification on this host; original network runtime remains an external dependency'))


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('create')
    for name in ('repository', 'stage', 'output'): c.add_argument('--'+name, type=Path, required=True)
    r = sub.add_parser('restore')
    for name in ('archive', 'output'): r.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); (create if a.action == 'create' else restore)(a)


if __name__ == '__main__': main()
