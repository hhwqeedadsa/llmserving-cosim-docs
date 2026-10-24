"""Bounded, offline-only stage runner. No network allowance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def load(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def usage(roots):
    paths = set()
    for root in roots:
        if root.is_file():
            paths.add(root)
        elif root.is_dir():
            paths.update(p for p in root.rglob('*') if p.is_file())
    return sum(p.stat().st_blocks*512 for p in paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    repo, stage = args.repository.resolve(), args.stage.resolve()
    stage.mkdir(exist_ok=True)
    records = load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert args.label not in {row['label'] for row in records}
    assert all(row['status'] == 'terminal' for row in records)
    assert sum(load(path)['wall_seconds'] for path in stage.rglob('probe.json'))+70 < 240
    roots = [stage, repo/'fixtures/dependency_wakeup', repo/'python/moe_ub_cosim',
             repo/'tests/unit/dependency_wakeup_test.py', repo/'docs/tasks/dependency_wakeup_spec.md',
             repo/'docs/tasks/current.md', repo/'runs/dependency_wakeup_checkpoint_20261024',
             repo/'docs/reviews/依赖唤醒离线判别_20261024.md']
    assert usage(roots)+2**20 < 96*2**20
    for path, reserve in ((stage, 8*2**30), (repo, 64*2**20)):
        filesystem = os.statvfs(path)
        assert filesystem.f_bavail*filesystem.f_frsize > reserve
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    records.append(dict(label=args.label, command=command, cwd=str(repo), status='started', network=False))
    save(stage/'commands.json', records)
    env = dict(os.environ, PYTHONPATH=str(repo/'python')+':'+str(repo), PYTHONDONTWRITEBYTECODE='1')
    monitored = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(stage/(args.label+'-monitor')),
                 '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
                 '--artifact-bytes', str(32*2**20), '--artifact-root', str(stage), '--min-free-bytes', str(8*2**30),
                 '--', *command]
    execution = subprocess.run(monitored, cwd=repo, env=env, timeout=65)
    records[-1].update(status='terminal', exit_code=execution.returncode)
    save(stage/'commands.json', records)
    assert execution.returncode == 0, 'preserve failure; do not silently rerun or raise limits'


if __name__ == '__main__':
    main()
