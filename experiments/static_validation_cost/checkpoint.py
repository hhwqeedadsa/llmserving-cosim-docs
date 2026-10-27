"""Member-hashed offline recovery; never repeats performance measurements."""
import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from .data import admit, load, save, sha, runtime_check

SCHEMA = 'static-validation-cost-checkpoint/1'


def create(repo, root, archive):
    stage = root/'stage'; assert load(stage/'verification.json')['passed'] and not archive.exists()
    runtime_check(stage, repo)
    prep = load(stage/'preparation.json')
    assert all(sha(repo/n) == h for n, h in prep['code'].items())
    paths = {}
    for path in stage.rglob('*'):
        if not path.is_file() or path.is_symlink() or 'empty-cwd' in path.relative_to(stage).parts: continue
        relative = path.relative_to(root)
        if 'monitor' in relative.parts:
            monitor = stage/'monitor'/path.relative_to(stage/'monitor').parts[0]
            if not (monitor/'probe.json').exists(): continue
        paths['run/'+str(relative)] = path
    for monitor in (root/'monitor').iterdir():
        if (monitor/'probe.json').exists():
            for path in monitor.iterdir():
                if path.is_file(): paths['run/'+str(path.relative_to(root))] = path
    sources = set(prep['code']) | set(load(stage/'runtime-identity.json')['sources'])
    sources.update(str(p.relative_to(repo)) for p in (repo/'experiments/static_validation_cost').iterdir() if p.is_file())
    for name in sources: paths['source/'+name] = repo/name
    entries = {name: dict(bytes=p.stat().st_size, sha256=sha(p), mode=0o755 if os.access(p, os.X_OK) else 0o644)
               for name, p in paths.items()}
    assert len(entries) < 4095 and sum(v['bytes'] for v in entries.values()) < 512*2**20
    import json
    with tarfile.open(archive, 'x:gz', compresslevel=6) as target:
        for name, path in sorted(paths.items()):
            raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == entries[name]['sha256']
            info = tarfile.TarInfo(name); info.size = len(raw); info.mode = entries[name]['mode']
            target.addfile(info, io.BytesIO(raw))
        raw = (json.dumps(dict(schema_version=SCHEMA, files=entries), indent=2)+'\n').encode()
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o644
        target.addfile(info, io.BytesIO(raw))
    digest = sha(archive); admit(archive, digest, SCHEMA)
    save(root/'checkpoint.json', dict(sha256=digest, members=len(entries)+1,
        compressed_bytes=archive.stat().st_size, uncompressed_bytes=sum(v['bytes'] for v in entries.values()),
        original_networks=28, additional_networks_on_restore=0))


def restore(archive, digest, output):
    assert not output.exists(); files = admit(archive, digest, SCHEMA); output.mkdir()
    for name, (raw, mode) in files.items():
        path = output/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); path.chmod(mode)
    del files
    stage = output/'run/stage'; empty = output/'empty-cwd'; empty.mkdir()
    source = output/'source'; env = os.environ|{'PYTHONPATH': str(source/'python')+':'+str(source), 'PYTHONDONTWRITEBYTECODE': '1'}
    runtime_check(stage, source)
    assert all(sha(source/n) == h for n, h in load(stage/'preparation.json')['code'].items())
    commands = []
    def command(module, *args):
        argv = [sys.executable, '-P', '-B', '-m', 'experiments.static_validation_cost.'+module, *map(str, args)]
        row = dict(command=argv, cwd=str(empty), exit_code=None); commands.append(row); save(output/'commands.json', commands)
        p = subprocess.run(argv, cwd=empty, env=env, capture_output=True, text=True, timeout=50)
        row.update(exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr); save(output/'commands.json', commands)
        assert p.returncode == 0, row
    command('study', 'report', '--stage', stage, '--output', stage/'report-recomputed')
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (stage/'report'/name).read_bytes() == (stage/'report-recomputed'/name).read_bytes()
    command('verify', '--stage', stage, '--output', stage/'reverification.json')
    assert (stage/'verification.json').read_bytes() == (stage/'reverification.json').read_bytes()
    save(output/'recovery.json', dict(passed=True, archive_sha256=digest, copied_source=True, empty_cwd=True,
        member_hashes_checked=True, identical_summary_markdown_html=True, identical_independent_verification=True,
        network_measurements_repeated=0, new_networks=0, new_builds=0, historical_library_paths_required=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('action', choices=('create', 'restore'))
    for name in ('repository', 'root', 'archive', 'output'): p.add_argument('--'+name, type=Path)
    p.add_argument('--digest'); a = p.parse_args()
    if a.action == 'create': create(a.repository.resolve(), a.root.resolve(), a.archive.resolve())
    else: restore(a.archive.resolve(), a.digest, a.output.resolve())
