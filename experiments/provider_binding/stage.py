"""Bounded offline command runner; no network execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def usage(roots):
    seen=set();apparent=allocated=0
    for root in roots:
        if not root.exists():continue
        for p in [root,*root.rglob('*')]:
            assert not p.is_symlink();st=p.stat();key=st.st_dev,st.st_ino
            if key in seen:continue
            seen.add(key);allocated+=st.st_blocks*512
            if p.is_file():apparent+=st.st_size
    return max(apparent,allocated)


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();s.mkdir(exist_ok=True)
    rows=load(s/'commands.json') if (s/'commands.json').exists() else []
    assert a.label not in {r['label'] for r in rows} and all(r['status']=='terminal' for r in rows)
    reports=[load(s/(r['label']+'-monitor/probe.json')) for r in rows]
    assert sum(r['wall_seconds'] for r in reports)+70<300
    assert usage([s,repo/'experiments/provider_binding',repo/'runs/provider_binding_checkpoint_20261024'])+2**20<128*2**20
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    command=a.command[1:] if a.command[:1]==['--'] else a.command;assert command
    monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(a.label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
        '--artifact-bytes',str(64*2**20),'--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*command]
    rows.append(dict(label=a.label,command=command,monitor=monitor,cwd=str(repo),status='started',network=False));save(s/'commands.json',rows)
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    r=subprocess.run(monitor,cwd=repo,env=env,timeout=65)
    rows[-1].update(status='terminal',exit_code=r.returncode);save(s/'commands.json',rows)
    assert r.returncode==0,a.label+': preserve failure; no automatic retries'


if __name__=='__main__':main()
