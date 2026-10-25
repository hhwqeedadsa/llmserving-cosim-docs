"""Independent bounded host-observation stage: four actual networks maximum."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_wakeup.stage import load, save, sha, usage


def roots(repo,stage):
    return [stage,repo/'runs/session_host_timing_checkpoint_20261024',repo/'fixtures/session_host_timing',
        repo/'python/moe_ub_cosim',repo/'backend/rdma',repo/'tests/unit/session_host_timing_test.py',
        repo/'tests/unit/session_host_timing_disabled_test.cc',repo/'examples/modern_deepep_native/README.md',
        *[repo/name for name in ('docs/architecture.md','backend/README.md','docs/record_storage.md',
            'docs/tasks/current.md','docs/tasks/session_host_timing_spec.md',
            'docs/reviews/现代事务扫描开销与网络事件密度_20261025.md')]]


def run(repo,stage,label,command,network=False):
    stage.mkdir(exist_ok=True);persistent=repo/'runs/session_host_timing_checkpoint_20261024'
    persistent.mkdir(exist_ok=True)
    records=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {row['label'] for row in records} and all(row['status']=='terminal' for row in records)
    assert not network or sum(row['network'] for row in records)<4
    probes=[p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored']
    assert sum(load(p)['wall_seconds'] for p in probes)+70<600
    assert usage(roots(repo,stage))+2*2**20<512*2**20
    for path,reserve in ((stage,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(path);assert fs.f_bavail*fs.f_frsize>reserve
    command=list(map(str,command))
    records.append(dict(label=label,command=command,cwd=str(repo),network=network,status='started'))
    save(stage/'commands.json',records)
    monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
        '--artifact-bytes',str(128*2**20),'--artifact-root',str(stage),'--artifact-root',str(persistent),
        '--min-free-bytes',str(8*2**30),'--',*command]
    result=subprocess.run(monitor,cwd=repo,env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),
        PYTHONDONTWRITEBYTECODE='1'),timeout=65)
    records[-1].update(status='terminal',exit_code=result.returncode);save(stage/'commands.json',records)
    assert result.returncode==0,'preserve failure and diagnose; do not rerun unchanged network or raise budget'


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('--network',action='store_true')
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    run(a.repository.resolve(),a.stage.resolve(),a.label,a.command[1:] if a.command[:1]==['--'] else a.command,a.network)


if __name__=='__main__':main()
