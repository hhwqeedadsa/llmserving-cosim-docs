"""Six predeclared native byte microbenchmarks through public CLI commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','observed','plans','backend','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--expected-backend-sha256',default='136b22dc81e0b72d801575fe1d32f4f258a0c9648567c61400d02ddfbc218a4d',
                   help='Expected binary identity; an explicitly different build is a new execution, not the historical binary')
    a=p.parse_args()
    repo,obs,plans,backend,out=(getattr(a,n).resolve() for n in ('repo','observed','plans','backend','output'))
    assert not out.exists() and out.parent.is_dir()
    assert hashlib.sha256(backend.read_bytes()).hexdigest()==a.expected_backend_sha256
    cfg=json.loads((obs/'config.json').read_text());out.mkdir()
    env={**os.environ,'PYTHONPATH':str(repo/'python'),'PYTHONDONTWRITEBYTECODE':'1'}
    rows=[];started=time.monotonic()
    def cli(name,args):
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        before=time.monotonic();remaining=58-(before-started);assert remaining>0
        with (out/(name+'.stdout')).open('w') as stdout,(out/(name+'.stderr')).open('w') as stderr:
            proc=subprocess.run(cmd,cwd=out,env=env,stdout=stdout,stderr=stderr,timeout=min(55,remaining))
        rows.append(dict(name=name,command=cmd,cwd=str(out),exit_code=proc.returncode,wall_seconds=time.monotonic()-before))
        (out/'commands.json').write_text(json.dumps(rows,indent=2)+'\n')
        assert proc.returncode==0,name
    for rate in cfg['upper_rates']:
        topology=out/f'topology-{rate}'
        cli(f'topology-{rate}',['topology','--config',obs/f'topology-{rate}.json','--output',topology])
        for policy in cfg['policies']:
            name=f'{rate}-{policy}'
            cli(name,['rdma-stream-probe','--topology',topology,'--protocol',obs/'protocol.json',
                '--config',plans/(policy+'.json'),'--backend',backend,'--output',out/name])
    print(json.dumps(dict(completed=True,public_commands=len(rows),network_conditions=6)))


if __name__=='__main__':main()
