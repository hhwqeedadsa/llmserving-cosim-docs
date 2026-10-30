"""Compile functional memory/wakeup cores and run relevant Python admission checks."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[];started=time.monotonic();summaries={}
    def run(label,command):
        remaining=110-(time.monotonic()-started)
        if remaining<=0:raise TimeoutError('CPU check total deadline exhausted')
        before=time.monotonic()
        with (out/(label+'.stdout')).open('x') as stdout,(out/(label+'.stderr')).open('x') as stderr:
            result=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=remaining)
        commands.append(dict(label=label,argv=list(map(str,command)),cwd=str(ROOT),exit_code=result.returncode,wall_seconds=time.monotonic()-before))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if result.returncode:raise RuntimeError(label+' failed; preserve logs')
    helper=ROOT/'third_party/LLMServingSim/astra-sim/extern/helper'
    for name in ('atomic32_memory','remote_memory','session_wakeups'):
        run('build-'+name,['c++','-std=c++17','-Wall','-Wextra','-Werror','-pedantic','-Ibackend','-Ibackend/rdma','-I'+str(helper),
                           str(ROOT/'tests/unit'/(name+'_test.cc')),'-o',str(out/name)])
        run(name,[str(out/name)])
        summaries[name]=json.loads((out/(name+'.stdout')).read_bytes())
    for name in ('atomic32_input_test.py','*transactions_test.py','*session*test.py','*application_window*test.py'):
        run(name.replace('*','all'),[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p',name,'-v'])
    sources=['backend/rdma/remote_memory.hh','backend/rdma/remote_transactions.cc',
             'backend/rdma/transaction_session_validation.hh','python/moe_ub_cosim/scaleout_transactions.py']
    result=dict(passed=True,cores=summaries,network_execution=False,commands=len(commands),
                source_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources})
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
