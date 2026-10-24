"""Bounded, independently accounted follow-up functional validation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(repo,stage,label,command,*,network=False,expected=0,cwd=None,env=None):
    records=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {x['label'] for x in records}
    assert sum(load(p)['wall_seconds'] for p in stage.rglob('probe.json') if 'restored' not in p.parts)+70<480
    assert sum(x['network'] for x in records)<4 or not network
    cmd=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
        '--artifact-bytes',str(96*2**20),'--artifact-root',str(stage),'--min-free-bytes',str(8*2**30),'--',*map(str,command)]
    records.append(dict(label=label,command=cmd,cwd=str(cwd or repo),network=network,expected_exit=expected,status='started'))
    save(stage/'commands.json',records)
    environment=dict(os.environ,PYTHONPATH=str(repo/'python'),PYTHONDONTWRITEBYTECODE='1')
    if env:environment.update(env)
    done=subprocess.run(cmd,cwd=cwd or repo,env=environment,timeout=65)
    records[-1].update(status='terminal',exit_code=done.returncode);save(stage/'commands.json',records)
    assert done.returncode==expected,label+': unexpected failure preserved; no retry'
