"""Owned fixed-budget acceptance runner for modern native integration."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.context_transactions.stage import load,save,sha,usage


def monitors(s):return [p for p in s.rglob('probe.json') if p.relative_to(s).parts[0]!='restored']
def roots(repo,s):return [s,repo/'fixtures/modern_native',repo/'runs/modern_native_checkpoint_20261024',
    repo/'python/moe_ub_cosim',repo/'examples/modern_deepep_native',repo/'tests/unit/modern_native_test.py',
    *[repo/'docs'/p for p in ('tasks/current.md','tasks/modern_native_bridge_spec.md','architecture.md','model_validation.md',
                            'record_storage.md','user_guide.md','reviews/现代完整操作与真实网络回调接入_20261024.md')]]


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('--network',action='store_true');p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();s.mkdir(exist_ok=True)
    records=load(s/'commands.json') if (s/'commands.json').exists() else []
    assert a.label not in {r['label'] for r in records} and all(r['status']=='terminal' for r in records)
    assert not a.network or sum(r['network'] for r in records)<4
    assert sum(load(p)['wall_seconds'] for p in monitors(s))+70<600
    assert usage(roots(repo,s))+2*2**20<384*2**20
    for path,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(path);assert fs.f_bavail*fs.f_frsize>reserve
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    records.append(dict(label=a.label,command=command,cwd=str(repo),network=a.network,status='started'));save(s/'commands.json',records)
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    cmd=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(a.label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
        '--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*command]
    result=subprocess.run(cmd,cwd=repo,env=env,timeout=65)
    records[-1].update(status='terminal',exit_code=result.returncode);save(s/'commands.json',records)
    assert result.returncode==0,a.label+': preserve failure; do not raise limits or rerun blindly'


if __name__=='__main__':main()
