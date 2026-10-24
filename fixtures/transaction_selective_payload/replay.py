"""New source root / empty cwd workflow; --execute-byte-once uses network #8.

Without that flag, only existing evidence is audited. No transaction rerun.
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
def save(p,x):p.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('restored','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--execute-byte-once',action='store_true');a=p.parse_args()
    root=a.restored.resolve();out=a.output.resolve();source=root/'source';ev=root/'evidence'
    environment=dict(os.environ,PYTHONPATH=str(source/'python'),PYTHONDONTWRITEBYTECODE='1')
    if a.execute_byte_once:
        out.mkdir(exist_ok=False);(out/'cwd').mkdir();records=[]
        def run(label,cmd):
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                done=subprocess.run(list(map(str,cmd)),cwd=out/'cwd',env=environment,stdout=stdout,stderr=stderr,timeout=60)
            records.append(dict(label=label,command=list(map(str,cmd)),cwd=str(out/'cwd'),pythonpath=environment['PYTHONPATH'],exit_code=done.returncode))
            save(out/'commands.json',records);assert done.returncode==0,label
        cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
        run('topology',cli+['topology','--config',ev/'inputs/topology.json','--output',out/'topology'])
        assert sha(out/'topology/topology.json')==load(ev/'reference/identity.json')['topology_sha256']
        run('transaction-preflight',cli+['rdma-preflight','--topology',out/'topology','--protocol',ev/'inputs/middle-protocol.json',
            '--transactions',source/'examples/scaleout_rdma_transactions/selective-chain.json','--output',out/'preflight.json'])
        run('pure-byte-reference',cli+['rdma-stream-probe','--topology',out/'topology','--protocol',ev/'reference/protocol.json',
            '--config',ev/'reference/work.json','--backend',root/'runtime/bin/stream-probe','--output',out/'byte-reference'])
        run('transaction-offline',[sys.executable,'-B',source/'fixtures/transaction_selective_payload/audit.py','--stage',ev,'--output',out/'verification.json'])
    new=load(out/'byte-reference/result.json');old=load(ev/'reference/result.json')
    hashes={Path(p).name:sha(Path(p)) for p in new['loaded_libraries'] if Path(p).name.startswith('libns3')}
    assert len(hashes)==8 and hashes==load(ev/'reference/identity.json')['fixed_eight_library_hashes']
    assert all(Path(p).resolve().parent==root/'runtime/lib' for p in new['loaded_libraries'] if Path(p).name.startswith('libns3'))
    new['loaded_libraries']=old['loaded_libraries'];assert new==old,'complete no-receiver selective result changed'
    assert not any(e['kind']=='payload_delivered' for e in new['persistent_transport']['selective_repeat']['events'])
    pre=load(out/'preflight.json');assert pre['passed'] and pre['transactions_checked'] and pre['transaction_check']['passed'] and not pre['network_executed']
    assert (out/'verification.json').read_bytes()==(ev/'verification.json').read_bytes()
    save(out/'replay.json',dict(passed=True,new_python_root=True,empty_cwd=True,public_commands=load(out/'commands.json'),
        complete_pure_byte_selective_result_equal_except_verified_library_paths=True,eight_runtime_libraries_from_package=True,
        five_transaction_audits_byte_equal=True,selective_transaction_preflight=True,total_stage_network_executions=8,
        scope='same Linux host incremental native runtime; byte-only reference plus new transaction preflight/offline replay; no clean build or full MoE'))


if __name__=='__main__':main()
