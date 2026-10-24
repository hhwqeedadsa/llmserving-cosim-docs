"""Independent fixed budget for zero-network admission diagnosis."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_wakeup.stage import load, save, sha, usage


def run(repo,stage,label,command):
    stage.mkdir(exist_ok=True)
    persistent=repo/'runs/native_admission_cost_20261024'
    persistent.mkdir(exist_ok=True)
    journal=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert all(row['status']=='terminal' for row in journal)
    assert label not in {row['label'] for row in journal}
    seconds=sum(load(p)['wall_seconds'] for p in stage.glob('*-monitor/probe.json'))
    assert seconds+70<240
    assert usage([stage,persistent,repo/'fixtures/native_admission_cost'])+2**20<128*2**20
    assert os.statvfs(stage).f_bavail*os.statvfs(stage).f_frsize>8*2**30
    assert os.statvfs(repo).f_bavail*os.statvfs(repo).f_frsize>64*2**20
    command=list(map(str,command))
    journal.append(dict(label=label,command=command,cwd=str(repo),status='started',network=False))
    save(stage/'commands.json',journal)
    monitored=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
        '--artifact-bytes',str(64*2**20),'--artifact-root',str(stage),'--artifact-root',str(persistent),
        '--min-free-bytes',str(8*2**30),'--',*command]
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run(monitored,cwd=repo,env=env,timeout=65)
    journal[-1].update(status='terminal',exit_code=result.returncode)
    save(stage/'commands.json',journal)
    assert result.returncode==0,'preserve failure; no unchanged measurement rerun or budget increase'


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();command=a.command[1:] if a.command[:1]==['--'] else a.command
    run(a.repository.resolve(),a.stage.resolve(),a.label,command)


if __name__=='__main__':main()
