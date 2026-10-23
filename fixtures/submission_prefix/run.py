"""Bounded stage runner; cumulative phase limits include all previous monitors."""
import argparse
import json
from pathlib import Path
from moe_ub_cosim.host_probe import probe, disk_usage

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--stage',required=True)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args();root=a.root.resolve()
    previous=list(root.glob('*-monitor/probe.json'))+[Path('/dev/shm/moe-submission-prefix-build-monitor-20261023/probe.json')]
    used=sum(json.loads(f.read_text())['wall_seconds'] for f in previous if f.is_file())
    assert used<480 and max(disk_usage(root).values())<192*2**20
    cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    result=probe(cmd,root/(a.stage+'-monitor'),rss_bytes=2**30,address_bytes=2**31,
        artifact_bytes=192*2**20,wall_seconds=min(60,480-used),artifact_roots=[root],min_free_bytes=8*2**30)
    print(json.dumps(result));assert result['status']=='completed'
if __name__=='__main__':main()
