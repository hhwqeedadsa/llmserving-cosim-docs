"""Bounded research-only evidence archive and replay from a fresh source root."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from experiments.reordering_baseline.stage import load, save


SCHEMA = 'reordering-baseline-checkpoint/1'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def admit(archive):
    """Validate all metadata and contents before the caller creates any output."""
    assert archive.stat().st_size < 32*2**20
    payload = {}; total = 0
    with tarfile.open(archive, 'r:gz') as ar:
        for m in ar:
            path = PurePosixPath(m.name)
            assert len(payload) < 2048 and m.isfile() and not path.is_absolute()
            assert '..' not in path.parts and str(path) == m.name and '\\' not in m.name
            assert m.name not in ('', '.') and m.name not in payload and 0 <= m.size <= 16*2**20
            total += m.size; assert total < 128*2**20
            payload[m.name] = ar.extractfile(m).read()
            assert len(payload[m.name]) == m.size
    assert 'manifest.json' in payload
    assert not any(str(parent) in payload for name in payload for parent in PurePosixPath(name).parents if str(parent) != '.')
    manifest = json.loads(payload['manifest.json']); assert manifest['schema_version'] == SCHEMA
    assert set(manifest['files']) == set(payload)-{'manifest.json'}
    for name, entry in manifest['files'].items():
        data = payload[name]
        assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
    return payload, manifest


def create(a):
    repo = a.repository.resolve(); s = a.stage.resolve(); assert not a.output.exists()
    assert load(s/'run.json')['new_actual_networks'] == 3
    assert load(s/'verification-final.json')['passed'] and load(s/'source-train-verification.json')['passed']
    files = {}
    for directory in ('inputs', 'planned', 'topology', 'network', 'source-history', 'archive-safety'):
        for p in sorted((s/directory).rglob('*')):
            if p.is_file(): files['evidence/'+str(p.relative_to(s))] = p
    for p in sorted(s.iterdir()):
        if p.is_file() and p.suffix in ('.py', '.json'): files['evidence/'+p.name] = p
    for p in sorted(s.glob('*-monitor/*')):
        if p.is_file() and (p.parent/'probe.json').exists(): files['evidence/monitors/'+str(p.relative_to(s))] = p
    for p in sorted((repo/'experiments/reordering_baseline').iterdir()):
        if p.is_file(): files['source/experiments/reordering_baseline/'+p.name] = p
    for name in ('graph_summary.py', 'analyze.py', 'mechanism.py', 'checks.py', 'routing.py', 'verify_mechanism.py'):
        p = repo/'experiments/local_fault_baselines'/name
        files['source/experiments/local_fault_baselines/'+name] = p
    report = 'docs/reviews/经典乱序等待后的改路长尾与发送端约束_20261024.md'
    if (repo/report).exists(): files['source/'+report] = repo/report
    assert all(not p.is_symlink() and p.stat().st_size <= 16*2**20 for p in files.values())
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name, p in files.items()}
    assert len(entries)+1 <= 2048 and sum(v['bytes'] for v in entries.values()) < 128*2**20
    manifest = dict(schema_version=SCHEMA, files=entries, native_runtime_bundled=False)
    with tarfile.open(a.output, 'x:gz', compresslevel=6) as ar:
        for name, p in files.items():
            data = p.read_bytes(); assert hashlib.sha256(data).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(data); ar.addfile(info, io.BytesIO(data))
        data = (json.dumps(manifest, indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(data); ar.addfile(info, io.BytesIO(data))
    assert a.output.stat().st_size < 32*2**20
    admit(a.output)
    save(a.output.with_suffix('.receipt.json'), dict(sha256=sha(a.output), members=len(entries)+1,
        uncompressed_bytes=sum(v['bytes'] for v in entries.values())+len(data), compressed_bytes=a.output.stat().st_size,
        scope='standalone research code and six saved conditions, three new networks; native runtime external'))


def restore(a):
    assert not a.output.exists()
    payload, manifest = admit(a.archive)
    a.output.mkdir()
    for name, data in payload.items():
        p = a.output/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    root = a.output.resolve(); s = root/'evidence'; source = root/'source'; cwd = root/'empty-cwd'; cwd.mkdir()
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1'); commands = []
    steps = [
        ('analysis', 'analyze', ['--observed', s/'inputs/observed', '--planned', s/'planned', '--network', s/'network', '--baseline', s/'inputs/baseline']),
        ('verification-final', 'verify', ['--stage', s, '--analysis', root/'analysis.json']),
        ('source-train', 'train', ['--network', s/'network']),
        ('source-train-verification', 'verify_train', ['--network', s/'network', '--analysis', root/'source-train.json']),
        ('summary', 'summary', ['--analysis', root/'analysis.json', '--train', root/'source-train.json'])]
    for label, module, args in steps:
        cmd = [sys.executable, '-B', '-m', 'experiments.reordering_baseline.'+module, *map(str, args), '--output', str(root/(label+'.json'))]
        with (root/(label+'.stdout.log')).open('w') as stdout, (root/(label+'.stderr.log')).open('w') as stderr:
            r = subprocess.run(cmd, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=60)
        commands.append(dict(command=cmd, cwd=str(cwd), pythonpath=str(source), exit_code=r.returncode)); save(root/'commands.json', commands)
        assert r.returncode == 0, label+': offline replay failed'
        assert (root/(label+'.json')).read_bytes() == (s/(label+'.json')).read_bytes(), label+': output changed'
    save(root/'replay.json', dict(passed=True, archive_sha256=sha(a.archive), members_verified=len(payload),
        new_source_root=True, empty_cwd=True, five_reports_byte_identical=True, network_executions=0,
        pythonpath_scope='restored research source only; no platform, fixtures, external runtime or old repository imports',
        commands=commands, native_runtime_bundled=False))


def safety(a):
    """Small adversarial metadata/content checks; never extract malformed input."""
    assert not a.output.exists(); a.output.mkdir(); rejected = []
    for mode in ('parent', 'absolute', 'duplicate', 'symlink', 'prefix', 'oversize', 'hash', 'unlisted'):
        name = '../escape' if mode == 'parent' else '/escape' if mode == 'absolute' else 'evidence/file'
        body = b'a'; entries = {name: dict(bytes=1, sha256=hashlib.sha256(body).hexdigest())}
        if mode == 'hash': entries[name]['sha256'] = '0'*64
        if mode == 'unlisted': entries = {}
        path = a.output/(mode+'.tar.gz')
        with tarfile.open(path, 'x:gz') as ar:
            info = tarfile.TarInfo(name); info.size = 1
            if mode == 'symlink': info.type = tarfile.SYMTYPE; info.linkname = '/outside'; info.size = 0
            ar.addfile(info, io.BytesIO(body) if info.isfile() else None)
            if mode == 'duplicate': ar.addfile(info, io.BytesIO(body))
            if mode == 'prefix':
                other = name+'/child'; m = tarfile.TarInfo(other); m.size = 1; ar.addfile(m, io.BytesIO(body)); entries[other] = entries[name]
            if mode == 'oversize':
                m = tarfile.TarInfo('too-big'); m.size = 16*2**20+1
                # Header alone suffices: admission must reject before reading.
                ar.fileobj.write(m.tobuf())
            manifest = (json.dumps(dict(schema_version=SCHEMA, files=entries))+'\n').encode()
            m = tarfile.TarInfo('manifest.json'); m.size = len(manifest); ar.addfile(m, io.BytesIO(manifest))
        try: admit(path)
        except (AssertionError, KeyError, ValueError, tarfile.TarError): rejected.append(mode)
        else: raise AssertionError('unsafe archive admitted: '+mode)
    save(a.output/'verification.json', dict(passed=True, rejected=rejected, destination_created_by_admission=False))


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('create')
    for k in ('repository', 'stage', 'output'): c.add_argument('--'+k, type=Path, required=True)
    r = sub.add_parser('restore')
    for k in ('archive', 'output'): r.add_argument('--'+k, type=Path, required=True)
    sub.add_parser('safety').add_argument('--output', type=Path, required=True)
    a = p.parse_args(); dict(create=create, restore=restore, safety=safety)[a.action](a)


if __name__ == '__main__': main()
