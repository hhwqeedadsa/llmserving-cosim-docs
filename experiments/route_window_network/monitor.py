"""Bound offline route-window study stages and cumulative artifacts."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--stage',required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    if not __debug__:raise RuntimeError('Do not run with -O')
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    assert command and a.stage and '/' not in a.stage
    root=a.root.resolve();used=sum(json.loads(f.read_bytes())['wall_seconds'] for f in (root/'monitors').rglob('probe.json'))
    if used+120>900:raise SystemExit('Cumulative900-second budget would be exceeded')
    raise SystemExit(subprocess.call([sys.executable,'-B','-m','moe_ub_cosim.host_probe',
        '--output-dir',str(root/'monitors'/a.stage),'--wall-seconds','120',
        '--rss-bytes',str(2**30),'--address-bytes',str(2*2**30),
        '--artifact-bytes',str(512*2**20),'--artifact-root',str(root),
        '--artifact-root',str(Path(__file__).parent/'results/20261031'),
        '--min-free-bytes',str(8*2**30),'--',*command]))


if __name__=='__main__':main()
