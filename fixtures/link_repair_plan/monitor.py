"""Bound CPU-only planning acceptance; never launch a network simulator."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--stage', required=True)
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    root = a.root.resolve()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    assert command and a.stage and '/' not in a.stage
    used = sum(json.loads(p.read_bytes())['wall_seconds'] for p in (root/'monitors').glob('*/probe.json'))
    if used+60 > 300:
        raise SystemExit('Cumulative300-second CPU budget would be exceeded')
    raise SystemExit(subprocess.call([sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe',
        '--output-dir', str(root/'monitors'/a.stage), '--wall-seconds', '60',
        '--rss-bytes', str(512*2**20), '--address-bytes', str(2**30),
        '--artifact-bytes', str(128*2**20), '--artifact-root', str(root),
        '--min-free-bytes', str(8*2**30), '--', *command]))


if __name__ == '__main__':
    main()
