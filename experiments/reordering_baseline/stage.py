"""Research-owned bounded monitor; never imports platform fixtures or tests."""
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def run(repo,s,label,command,network=False,cwd=None,environment=None):
    rows=load(s/'commands.json') if (s/'commands.json').exists() else []
    assert label not in {r['label'] for r in rows}
    assert sum(r['network'] for r in rows)<3 or not network
    assert sum(load(p)['wall_seconds'] for p in s.rglob('probe.json') if 'restored' not in p.parts)+70<600
    cmd=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(s/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
        '--artifact-root',str(s),'--min-free-bytes',str(8*2**30),'--',*map(str,command)]
    env=dict(os.environ,PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    if environment:env.update(environment)
    rows.append(dict(label=label,command=cmd,cwd=str(cwd or repo),network=network,status='started'));save(s/'commands.json',rows)
    done=subprocess.run(cmd,cwd=cwd or repo,env=env,timeout=65)
    rows[-1].update(status='terminal',exit_code=done.returncode);save(s/'commands.json',rows)
    assert done.returncode==0,label+': failure preserved, no retry'
