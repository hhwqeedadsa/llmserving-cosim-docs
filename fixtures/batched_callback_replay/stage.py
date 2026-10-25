"""Independent zero-network resource budget for saved callback batch analysis."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_wakeup.stage import load,save,sha,usage


def roots(repo,stage):
    return [stage,repo/'runs/batched_callback_replay_checkpoint_20261025',repo/'fixtures/batched_callback_replay',
        repo/'python/moe_ub_cosim',repo/'tests/unit/batched_callback_replay_test.py',
        repo/'docs/tasks/current.md',repo/'docs/tasks/batched_callback_replay_spec.md',
        repo/'docs/reviews/保留回调批次时刻的交互判别_20261025.md']


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    repo=a.repository.resolve();stage=a.stage.resolve();stage.mkdir(exist_ok=True)
    persistent=repo/'runs/batched_callback_replay_checkpoint_20261025';persistent.mkdir(exist_ok=True)
    rows=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert a.label not in {row['label'] for row in rows} and all(row['status']=='terminal' for row in rows)
    probes=[path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored']
    assert sum(load(path)['wall_seconds'] for path in probes)+70<240
    assert usage(roots(repo,stage))+2**20<96*2**20
    for path,reserve in ((stage,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(path);assert fs.f_bavail*fs.f_frsize>reserve
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    rows.append(dict(label=a.label,command=command,cwd=str(repo),network=False,status='started'))
    save(stage/'commands.json',rows)
    result=subprocess.run([sys.executable,'-B','-m','moe_ub_cosim.host_probe',
        '--output-dir',str(stage/(a.label+'-monitor')),'--wall-seconds','60','--rss-bytes',str(2**30),
        '--address-bytes',str(2**31),'--artifact-bytes',str(48*2**20),
        '--artifact-root',str(stage),'--artifact-root',str(persistent),'--min-free-bytes',str(8*2**30),
        '--',*command],cwd=repo,timeout=65)
    rows[-1].update(status='terminal',exit_code=result.returncode);save(stage/'commands.json',rows)
    assert result.returncode==0,'preserve failure; no silent rerun or limit increase'


if __name__=='__main__':main()
