"""Rebuild the CPU recording harness and recompute a restored source audit offline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();e=a.evidence.resolve();out=a.output.resolve();tool=Path(__file__).resolve().parent
    out.mkdir();cwd=out/'empty-cwd';cwd.mkdir();env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH',None)
    commands=[]
    def run(name,args):
        command=[sys.executable,'-B',str(tool/(name+'.py')),*map(str,args)]
        with (out/(name+'.stdout')).open('xb') as stdout,(out/(name+'.stderr')).open('xb') as stderr:
            result=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=20)
        commands.append(dict(command=command,cwd=str(cwd),exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(commands,sort_keys=True,indent=2)+'\n')
        if result.returncode:raise RuntimeError(name+' failed; preserved logs in '+str(out))
    run('build',['--inputs',e/'inputs','--output',out/'build'])
    with (out/'harness.json').open('xb') as stdout,(out/'harness.stderr').open('xb') as stderr:
        result=subprocess.run([str(out/'build/source-harness')],cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=10)
    commands.append(dict(command=[str(out/'build/source-harness')],cwd=str(cwd),exit_code=result.returncode))
    (out/'commands.json').write_text(json.dumps(commands,sort_keys=True,indent=2)+'\n')
    assert result.returncode==0,'restored recording harness failed'
    run('analyze',['--inputs',e/'inputs','--build',out/'build','--harness',out/'harness.json','--output',out/'summary.json'])
    run('verify',['--inputs',e/'inputs','--build',out/'build','--summary',out/'summary.json','--output',out/'verification.json'])
    run('correction',['--inputs',e/'inputs','--build',e/'build','--output',out/'correction.json'])
    equal={}
    for original,replayed in [('build/selected.c','build/selected.c'),
            ('source-run-monitor/stdout.log','harness.json'),('summary.json','summary.json'),
            ('verification.json','verification.json'),('correction.json','correction.json')]:
        left=(e/original).read_bytes();right=(out/replayed).read_bytes()
        assert left==right,'replay differs: '+replayed
        equal[replayed]=hashlib.sha256(right).hexdigest()
    receipt=dict(schema_version='target-retirement-replay/1',byte_equal=equal,
        actual_kernel_or_hardware_executed=False,new_ns3_networks=0,
        requires='host Python standard library, gcc, libc; recording substitutes only',commands=len(commands))
    (out/'replay.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
