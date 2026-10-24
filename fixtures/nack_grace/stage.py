"""This platform stage's bounded command recorder; no simulation on import."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(Path(p).read_text())
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(repo,stage,label,command,cwd=None,network=False,env=None):
    records=load(stage/'commands.json') if (stage/'commands.json').exists() else []
    assert label not in {r['label'] for r in records}
    assert sum(load(p)['wall_seconds'] for p in stage.rglob('probe.json'))+70<720
    assert sum(r['network'] for r in records)<6 or not network
    command=list(map(str,command));cwd=Path(cwd or repo)
    monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
        '--wall-seconds','60','--rss-bytes',str(2**30),'--address-bytes',str(2**31),
        '--artifact-bytes',str(128*2**20),'--artifact-root',str(stage),'--min-free-bytes',str(8*2**30),'--',*command]
    records.append(dict(label=label,command=monitor,cwd=str(cwd),network=network,status='started'))
    save(stage/'commands.json',records)
    environment=dict(os.environ,PYTHONPATH=str(repo/'python'),PYTHONDONTWRITEBYTECODE='1')
    if env:environment.update(env)
    done=subprocess.run(monitor,cwd=cwd,env=environment,timeout=65)
    records[-1].update(status='terminal',exit_code=done.returncode);save(stage/'commands.json',records)
    assert done.returncode==0,label+': failure preserved; stop without retry'
