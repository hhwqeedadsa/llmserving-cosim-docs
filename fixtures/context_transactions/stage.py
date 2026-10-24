"""Owned bounded platform acceptance runner; never installs/builds/downloads."""
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


def monitors(stage):
    # Restored evidence is a copy, never an additional execution receipt.
    return [p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored' and
            p.parent.name!='runtime' and load(p).get('wall_seconds') is not None]


def storage_roots(repo,stage):
    return [stage,repo/'fixtures/context_transactions',repo/'runs/context_transactions_checkpoint_20261024',
        repo/'python/moe_ub_cosim/context_transactions.py',repo/'python/moe_ub_cosim/cli.py',
        repo/'tests/unit/context_transactions_test.py',repo/'examples/scaleout_context_transactions',
        *[repo/'docs'/p for p in ('architecture.md','model_validation.md','record_storage.md','user_guide.md',
            'tasks/current.md','tasks/context_transaction_binding_spec.md',
            'reviews/Context连接身份绑定与实包恢复验收_20261024.md')]]


def main():
    p=argparse.ArgumentParser()
    for n in ('repository','stage'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--label',required=True);p.add_argument('--network',action='store_true');p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();s.mkdir(exist_ok=True)
    commands=load(s/'commands.json') if (s/'commands.json').exists() else []
    assert a.label not in {r['label'] for r in commands} and all(r['status']=='terminal' for r in commands)
    assert not a.network or sum(r.get('network_started',r['network']) for r in commands)<3
    reports=[load(p) for p in monitors(s)]
    assert sum(r['wall_seconds'] for r in reports)+70<450
    assert usage(storage_roots(repo,s))+2**20<256*2**20
    for root,reserve in ((s,8*2**30),(repo,64*2**20)):
        fs=os.statvfs(root);assert fs.f_bavail*fs.f_frsize>reserve
    command=a.command[1:] if a.command[:1]==['--'] else a.command;assert command
    args=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(a.label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(96*2**20),
        '--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*command]
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    commands.append(dict(label=a.label,command=command,cwd=str(repo),network=a.network,status='started'));save(s/'commands.json',commands)
    r=subprocess.run(args,cwd=repo,env=env,timeout=65)
    commands[-1].update(status='terminal',exit_code=r.returncode);save(s/'commands.json',commands)
    assert r.returncode==0,a.label+': preserve failure; no automatic retry or higher limit'


if __name__=='__main__':main()
