"""Bounded source/evidence snapshots and hash-before-write extraction."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def create(a):
    repo = a.repository.resolve(); s = a.stage.resolve(); files = {}; assert not a.output.exists()
    for prefix in ('python', 'backend/rdma', 'fixtures/live_ecmp', 'examples/scaleout_live_ecmp'):
        for p in (repo/prefix).rglob('*'):
            if p.is_file() and p.suffix in ('.py', '.cc', '.hh', '.json', '.md'):
                files['source/'+str(p.relative_to(repo))] = p
    files['source/fixtures/selective_repeat/audit.py'] = repo/'fixtures/selective_repeat/audit.py'
    for relative in ('AGENTS.md', 'docs/development.md', 'docs/architecture.md', 'docs/record_storage.md',
                     'docs/user_guide.md', 'backend/README.md', 'docs/tasks/live_ecmp_spec.md',
                     'tests/unit/scaleout_link_failures_test.py',
                     'docs/reviews/本地端口感知与故障恢复强基线_20261024.md'):
        files['source/'+relative] = repo/relative
    for directory in ('inputs', 'inputs-corrected', 'topology', 'network', 'native-reject-inputs', 'unsafe-archive-cases', 'fresh'):
        for p in (s/directory).rglob('*'):
            if p.is_file(): files['evidence/'+str(p.relative_to(s))] = p
    for p in s.iterdir():
        if p.is_file() and p.suffix in ('.json', '.py', '.cc'): files['evidence/'+p.name] = p
    for p in s.glob('*-monitor/*'):
        if p.is_file() and (p.parent/'probe.json').exists(): files['evidence/monitors/'+str(p.relative_to(s))] = p
    assert all(not p.is_symlink() and p.stat().st_size <= 16*2**20 for p in files.values())
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p)) for name, p in files.items()}
    assert len(entries)+1 <= 2048 and sum(x['bytes'] for x in entries.values()) < 128*2**20
    manifest = dict(schema_version='moe-live-ecmp-checkpoint/1', files=entries, native_runtime_bundled=False)
    with tarfile.open(a.output, 'x:gz', compresslevel=6) as ar:
        for name, p in files.items():
            data = p.read_bytes(); assert hashlib.sha256(data).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(data); ar.addfile(info, io.BytesIO(data))
        data = (json.dumps(manifest, indent=2)+'\n').encode(); info = tarfile.TarInfo('manifest.json'); info.size = len(data); ar.addfile(info, io.BytesIO(data))
    assert a.output.stat().st_size < 32*2**20
    save(a.output.with_suffix('.receipt.json'), dict(sha256=sha(a.output), members=len(files)+1,
        uncompressed_bytes=sum(x['bytes'] for x in entries.values())+len(data), compressed_bytes=a.output.stat().st_size,
        scope='source and evidence; native program/libraries are explicit external hash-verified inputs'))


def restore(a):
    assert not a.output.exists()
    assert a.archive.stat().st_size < 32*2**20
    with tarfile.open(a.archive, 'r:gz') as ar:
        members = ar.getmembers(); assert len(members) <= 2048; names = set(); total = 0
        for m in members:
            p = PurePosixPath(m.name)
            assert m.isfile() and not p.is_absolute() and str(p) == m.name and '..' not in p.parts
            assert m.name not in ('', '.') and '\\' not in m.name and m.name not in names and 0 <= m.size <= 16*2**20
            names.add(m.name); total += m.size
        assert total < 128*2**20 and 'manifest.json' in names
        assert not any(str(parent) in names for name in names for parent in PurePosixPath(name).parents if str(parent) != '.')
        manifest = json.load(ar.extractfile('manifest.json'))
        assert manifest['schema_version'] == 'moe-live-ecmp-checkpoint/1' and set(manifest['files']) == names-{'manifest.json'}
        for name, entry in manifest['files'].items():
            data = ar.extractfile(name).read(); assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
        a.output.mkdir()
        for m in members:
            p = a.output/m.name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(ar.extractfile(m).read())
    save(a.output/'restore.json', dict(passed=True, archive_sha256=sha(a.archive), members_verified=len(members), network_executions=0))


def main():
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('create')
    for k in ('repository', 'stage', 'output'): c.add_argument('--'+k, type=Path, required=True)
    r = sub.add_parser('restore')
    for k in ('archive', 'output'): r.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); (create if a.action == 'create' else restore)(a)


if __name__ == '__main__': main()
