"""Bound read-only audit stages without depending on shell Python paths."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--stage',required=True)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    root=a.root.resolve();root.mkdir(parents=True,exist_ok=True)
    used=sum(json.loads(p.read_bytes())['wall_seconds'] for p in (root/'monitors').glob('*/probe.json'))
    assert used+60<=300 and '/' not in a.stage
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[2]/'python')
    raise SystemExit(subprocess.call([sys.executable,'-B','-m','moe_ub_cosim.host_probe',
      '--output-dir',str(root/'monitors'/a.stage),'--wall-seconds','60','--rss-bytes',str(512*2**20),
      '--address-bytes',str(2**30),'--artifact-bytes',str(64*2**20),'--artifact-root',str(root),
      '--artifact-root',str(Path(__file__).parent/'results/20261030'),'--min-free-bytes',str(8*2**30),
      '--',*command],env=env))


if __name__=='__main__':main()
