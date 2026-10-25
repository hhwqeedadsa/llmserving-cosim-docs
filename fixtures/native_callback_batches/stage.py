"""Five-network platform budget independent of closed earlier stages."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.native_wakeup.stage import load,save,sha,usage


def roots(repo,stage):
    return [stage,repo/'runs/native_callback_batches_checkpoint_20261025',repo/'fixtures/native_callback_batches',
        repo/'backend/rdma',repo/'python/moe_ub_cosim',repo/'tests/unit/native_callback_batches_test.py',repo/'tests/unit/session_callback_batches_test.cc',repo/'tests/unit/session_host_timing_test.py',repo/'docs/user_guide.md',repo/'docs/tasks/current.md',repo/'docs/tasks/native_callback_batches_spec.md',
        *[repo/name for name in ('docs/architecture.md','backend/README.md','docs/record_storage.md',
            'examples/modern_deepep_native/README.md','docs/reviews/原生批次交付与四卡完整调用_20261025.md')]]


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('--network',action='store_true')
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    repo=a.repository.resolve();stage=a.stage.resolve();stage.mkdir(exist_ok=True)
    persistent=repo/'runs/native_callback_batches_checkpoint_20261025';persistent.mkdir(exist_ok=True)
    records=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert a.label not in {row['label'] for row in records} and all(row['status']=='terminal' for row in records)
    assert not a.network or sum(row['network'] for row in records)<5
    probes=[path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored']
    assert sum(load(path)['wall_seconds'] for path in probes)+70<600
    assert usage(roots(repo,stage))+2*2**20<512*2**20
    for path,reserve in ((stage,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(path);assert fs.f_bavail*fs.f_frsize>reserve
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    records.append(dict(label=a.label,command=command,cwd=str(repo),network=a.network,status='started'))
    save(stage/'commands.json',records)
    result=subprocess.run([sys.executable,'-B','-m','moe_ub_cosim.host_probe',
        '--output-dir',str(stage/(a.label+'-monitor')),'--wall-seconds','60',
        '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
        '--artifact-root',str(stage),'--artifact-root',str(persistent),'--min-free-bytes',str(8*2**30),
        '--',*command],cwd=repo,timeout=65)
    records[-1].update(status='terminal',exit_code=result.returncode);save(stage/'commands.json',records)
    assert result.returncode==0,'preserve failure; no unchanged network rerun or limit increase'


if __name__=='__main__':main()
