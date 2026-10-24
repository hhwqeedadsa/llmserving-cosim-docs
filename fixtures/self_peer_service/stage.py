"""Owned bounded self-service platform driver; no dependency rebuild/download."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.context_transactions.stage import load,save,sha,usage


def monitors(stage):return [p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored']
def roots(repo,stage):return [stage,repo/'fixtures/self_peer_service',repo/'runs/self_peer_service_checkpoint_20261024',repo/'backend/rdma',repo/'python/moe_ub_cosim',repo/'examples/scaleout_self_peer',repo/'tests/unit/self_peer_service_test.py',
    *[repo/p for p in ('backend/README.md','docs/architecture.md','docs/model_validation.md','docs/record_storage.md',
        'docs/user_guide.md','docs/tasks/current.md','docs/tasks/self_peer_service_spec.md',
        'docs/reviews/同节点服务与远端通信共享内存验收_20261024.md')]]


def run(repo,stage,label,command,*,network=False,cwd=None,expected=0):
    repo=repo.resolve();s=stage.resolve();s.mkdir(exist_ok=True);cwd=cwd or repo
    records=load(s/'commands.json') if (s/'commands.json').exists() else []
    assert label not in {r['label'] for r in records} and all(r['status']=='terminal' for r in records)
    assert not network or sum(r['network'] for r in records)<4
    assert sum(load(p)['wall_seconds'] for p in monitors(s))+70<600
    assert usage(roots(repo,s))+2*2**20<384*2**20
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    command=list(map(str,command));record=dict(label=label,command=command,cwd=str(cwd),network=network,status='started',expected_exit=expected)
    records.append(record);save(s/'commands.json',records)
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    args=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
        '--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*command]
    result=subprocess.run(args,cwd=cwd,env=env,timeout=65)
    record.update(status='terminal',exit_code=result.returncode);save(s/'commands.json',records)
    assert result.returncode==expected,label+': preserve failure; do not raise limits or blindly retry'


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('--network',action='store_true');p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    run(a.repository,a.stage,a.label,a.command[1:] if a.command[:1]==['--'] else a.command,network=a.network)


if __name__=='__main__':main()
