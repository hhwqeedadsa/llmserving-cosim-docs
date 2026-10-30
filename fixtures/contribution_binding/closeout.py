"""Audit saved execution evidence and collect resources without rerunning networks."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    run = ROOT / 'runs/contribution_binding_20261030'
    receipt = load(HERE / 'receipt.json')
    for name, digest in receipt['platform_source_sha256'].items():
        assert sha(ROOT / name) == digest, name
    for name, digest in receipt['run_sha256'].items():
        assert sha(run / name) == digest, name
    assert (HERE / 'verification.json').read_bytes() == (run / 'final-independent/verification.json').read_bytes()
    assert (run / 'legacy-workflow/full/report.json').read_bytes() == (ROOT / 'fixtures/contribution_attempts/example-report.json').read_bytes()
    # Only authoritative receipts; relocated runs contain copied worker receipts.
    paths = sorted((run / 'monitors').glob('*/probe.json'))
    paths += [run / mode / 'execution/probe.json' for mode in ('normal', 'local')]
    monitors = {str(path.relative_to(run)): load(path) for path in paths}
    assert len(monitors) == 11
    assert all(row['status'] == 'completed' and row['child_exit_code'] == 0
               and not row['remaining_live_group_pids'] and row.get('ended_utc')
               for row in monitors.values())
    groups = {row['process_group'] for row in monitors.values()}
    live = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            # stat comm may contain spaces or parentheses.
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) in groups and fields[0] != 'Z':
                live.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    assert not live, live
    duration = sum(row['wall_seconds'] for row in monitors.values())
    peak = max(row['peak_sampled_group_rss_bytes'] for row in monitors.values())
    assert duration < 900 and peak < 2**30
    seen = set()
    apparent = allocated = 0
    for base, dirs, files in os.walk(run):
        for name in [None, *files]:
            path = Path(base) if name is None else Path(base) / name
            stat = path.lstat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            allocated += stat.st_blocks * 512
            if name is not None:
                apparent += stat.st_size
    free = shutil.disk_usage(run).free
    assert max(apparent, allocated) < 512 * 2**20 and free >= 8 * 2**30
    for path in [*HERE.glob('*.py'), ROOT / 'tests/integration/check_contribution_binding.py']:
        ast.parse(path.read_text())
    dep = subprocess.run(['bash', 'scripts/check_deps.sh'], cwd=ROOT,
                         capture_output=True, text=True, timeout=20)
    assert dep.returncode == 0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    links = []
    for path in [HERE / 'README.md', HERE / 'report.html', ROOT / 'docs/contribution_binding.md',
                 ROOT / 'examples/scaleout_contribution_tracking/README.md']:
        hrefs = re.findall(r'\]\(([^)]+)\)', path.read_text())
        hrefs += re.findall(r'href="([^"]+)"', path.read_text())
        for href in hrefs:
            url = urlsplit(href)
            if url.scheme or not url.path:
                continue
            target = (path.parent / unquote(url.path)).resolve()
            assert target.is_file() or target == HERE / 'resources.json', target
            links.append(str(target.relative_to(ROOT)))
    resources = dict(passed=True, monitors=monitors, cumulative_seconds=duration,
                     sampled_peak_group_rss_bytes=peak, run_apparent_bytes=apparent,
                     run_allocated_bytes=allocated, volume_free_bytes=free,
                     remaining_live_group_pids=live, new_network_executions=2,
                     builds=0, gpu_executions=0, dependency_changes=False,
                     scope='saved authoritative monitors; nested worker durations conservatively summed; relocated copies excluded; read/edit and this offline collector are not timed')
    audit = dict(passed=True, execution_commit=receipt['execution_commit'],
                 source_and_run_hashes_match=True, legacy_report_byte_identical=True,
                 independent_verification_byte_identical=True, dependency_check=dep.stdout,
                 links=links, collector_sha256=sha(Path(__file__)),
                 scope='offline closeout only; no network executions or recovery gain')
    for name, value in [('resources.json', resources), ('final-audit.json', audit)]:
        with (HERE / name).open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps({k: v for k, v in resources.items() if k != 'monitors'}))


if __name__ == '__main__':
    main()
