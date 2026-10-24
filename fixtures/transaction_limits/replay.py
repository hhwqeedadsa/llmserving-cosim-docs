"""Fresh Python root/cwd workflow and same-library default regression.

The optional native reference consumes the LAST (eighth) predeclared execution.
Without --execute-reference-once this script only audits existing outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--execute-reference-once',action='store_true')
    a=p.parse_args();root=a.restored.resolve();out=a.output.resolve();source=root/'source';ev=root/'evidence'
    environment=dict(os.environ,PYTHONPATH=str(source/'python'),PYTHONDONTWRITEBYTECODE='1')
    if a.execute_reference_once:
        out.mkdir(exist_ok=False);(out/'cwd').mkdir();records=[]
        def run(label,tail):
            command=[sys.executable,'-B','-m','moe_ub_cosim',*map(str,tail)]
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                result=subprocess.run(command,cwd=out/'cwd',env=environment,stdout=stdout,stderr=stderr,timeout=60)
            records.append(dict(command=command,cwd=str(out/'cwd'),pythonpath=environment['PYTHONPATH'],exit_code=result.returncode))
            save(out/'commands.json',records);assert result.returncode==0,label
        run('topology',['scaleout','topology','--config',ev/'inputs/topology.json','--output',out/'topology'])
        run('expanded-preflight',['scaleout','rdma-preflight','--topology',out/'topology',
            '--protocol',ev/'inputs/protocol.json','--transactions',ev/'inputs/expanded.json','--output',out/'preflight.json'])
        run('reference',['scaleout','rdma-transaction-probe','--topology',out/'topology',
            '--protocol',ev/'inputs/protocol.json','--config',source/'examples/scaleout_rdma_transactions/sparse-windows.json',
            '--backend',root/'runtime/bin/reference-probe','--output',out/'reference'])
    old=load(out/'reference/result.json');new=load(ev/'network/legacy-new/result.json')
    libs_old={Path(p).name:sha(Path(p)) for p in old['loaded_libraries'] if Path(p).name.startswith('libns3')}
    libs_new={Path(p).name:sha(root/'runtime/lib'/Path(p).name) for p in new['loaded_libraries'] if Path(p).name.startswith('libns3')}
    assert len(libs_old)==8 and libs_old==libs_new
    assert all(Path(p).resolve().parent==root/'runtime/lib' for p in old['loaded_libraries'] if Path(p).name.startswith('libns3'))
    old['loaded_libraries']=new['loaded_libraries'];assert old==new
    pre=load(out/'preflight.json')
    assert pre['passed'] and pre['transactions_checked'] and pre['transaction_check']['passed'] and not pre['network_executed']
    summaries=load(ev/'verification.json')['conditions'];offline=out/'offline';offline.mkdir(exist_ok=True)
    cases=[('expanded-corrected','expanded','expanded-corrected'),('long-fault','long-recovery','long-fault-configured'),('online','expanded','online/api')]
    for name,work,directory in cases:
        command=[sys.executable,'-B',str(source/'fixtures/transaction_limits/audit.py'),
            '--result',str(ev/'network'/directory/'result.json'),'--work',str(ev/'inputs'/(work+'.json')),'--output',str(offline/(name+'.json'))]
        subprocess.run(command,cwd=out/'cwd',env=environment,check=True,timeout=10,capture_output=True)
        assert (offline/(name+'.json')).read_bytes()==(json.dumps(summaries[name],indent=2)+'\n').encode()
    save(out/'replay.json',dict(passed=True,new_python_root=True,empty_cwd=True,public_commands=load(out/'commands.json'),
        same_library_head_reference_default_full_result_equal=True,eight_runtime_libraries_from_package=True,
        offline_audits_byte_equal=3,total_stage_network_executions=8,
        scope='same Linux host incremental binaries; expanded config preflight plus old/new default network comparison; no clean dependency build'))


if __name__=='__main__':main()
