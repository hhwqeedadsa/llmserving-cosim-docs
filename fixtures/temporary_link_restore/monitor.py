"""Bounded functional acceptance stages for temporary switch-link restoration."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--stage', required=True)
    p.add_argument('--build', action='store_true')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    assert command and a.stage and '/' not in a.stage
    root = a.root.resolve()
    used = sum(json.loads(f.read_bytes())['wall_seconds'] for f in (root/'monitors').glob('*/probe.json'))
    seconds = 900 if a.build else 120
    if used+seconds > 1800:
        raise SystemExit('Would exceed cumulative1800-second budget')
    raise SystemExit(subprocess.call([sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe',
        '--output-dir', str(root/'monitors'/a.stage), '--wall-seconds', str(seconds),
        '--rss-bytes', str((2 if a.build else 1)*2**30), '--address-bytes', str((4 if a.build else 2)*2**30),
        '--artifact-bytes', str(2*2**30), '--artifact-root', str(root),
        '--min-free-bytes', str(8*2**30), '--', *command]))


if __name__ == '__main__':
    main()
