"""Bounded local evidence archive and empty-cwd offline analysis replay."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--expected-analysis', type=Path, help='Completed analysis to reproduce; defaults to input/analysis-final')
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    start = time.monotonic(); root = a.input.resolve(); out = a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free >= 8*2**30+64*2**20
    out.mkdir(); snapshot = out/'snapshot'; snapshot.mkdir()
    exp = json.loads((root/'experiment.json').read_text())
    paths = []
    experiments = [(root, exp)]
    if (root/'bootstrap80/experiment.json').exists():
        experiments.append((root/'bootstrap80', json.loads((root/'bootstrap80/experiment.json').read_text())))
    for location, definition in experiments:
        paths += list(location.glob('*.json'))
        for case in definition['cases']:
            directory = location/case['name']
            paths += list(directory.glob('*.json'))
            paths += list((directory/'graphs').glob('*/wave.json'))
            paths += [directory/'execution/probe.json']
    for source in paths:
        target = snapshot/'input'/source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    for source in HERE.iterdir():
        if source.is_file() and source.suffix in ('.md', '.json', '.py'):
            target = snapshot/'source/experiments/recovery_delay'/source.name
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    for name in ('summary.json', 'results.md', 'report.html'):
        target = snapshot/'expected'/name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile((a.expected_analysis or root/'analysis-final')/name, target)
    members = {str(p.relative_to(snapshot)): digest(p) for p in snapshot.rglob('*') if p.is_file()}
    (snapshot/'members.json').write_text(json.dumps(members, sort_keys=True, indent=2)+'\n')
    uncompressed = sum(p.stat().st_size for p in snapshot.rglob('*') if p.is_file())
    assert uncompressed < 32*2**20
    archive = out/'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in sorted(snapshot.rglob('*')):
            if path.is_file(): tar.add(path, arcname=str(path.relative_to(snapshot)), recursive=False)
    assert archive.stat().st_size < 16*2**20
    restored = out/'restored'; restored.mkdir()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            assert member.isfile() and not Path(member.name).is_absolute() and '..' not in Path(member.name).parts
        tar.extractall(restored, filter='data')
    for path, expected in members.items(): assert digest(restored/path) == expected
    empty = out/'empty-cwd'; empty.mkdir()
    command = [sys.executable, '-B', str(restored/'source/experiments/recovery_delay/analyze.py'),
               '--input', str(restored/'input'), '--output', str(out/'analysis')]
    with (out/'stdout.log').open('w') as stdout, (out/'stderr.log').open('w') as stderr:
        result = subprocess.run(command, cwd=empty, stdout=stdout, stderr=stderr, timeout=30, check=False)
    assert result.returncode == 0
    equal = {name: digest(restored/'expected'/name) == digest(out/'analysis'/name)
             for name in ('summary.json', 'results.md', 'report.html')}
    assert all(equal.values())
    total = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    assert total < 256*2**20 and time.monotonic()-start < 60
    report = dict(passed=True, archive_members_checked=len(members), uncompressed_bytes=uncompressed,
                  archive_bytes=archive.stat().st_size, archive_sha256=digest(archive),
                  command=command, cwd=str(empty), exit_code=result.returncode, outputs_equal=equal,
                  wall_seconds=time.monotonic()-start, peak_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                  task_tree_bytes=total, network_rerun=False, gpu_executed=False,
                  storage='tmpfs, volatile; no persistent backup',
                  archive_scope='offline packet/timer analysis; no runtime binaries, libraries or Python packages')
    (out/'reproduction.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__': main()
