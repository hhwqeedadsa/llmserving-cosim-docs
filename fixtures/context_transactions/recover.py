"""Run only from restored Python source, empty cwd, and bundled native runtime."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from fixtures.context_transactions.audit import audit
from fixtures.context_transactions.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--restored',type=Path,required=True);p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    root=a.restored.resolve();s=a.stage.resolve();source=root/'source';evidence=root/'evidence';cwd=root/'empty-cwd'
    assert Path(__file__).resolve().is_relative_to(source) and not any(cwd.iterdir())
    env=dict(os.environ,PYTHONPATH=str(source/'python')+':'+str(source),PYTHONDONTWRITEBYTECODE='1')
    commands=[]
    def invoke(label,argv):
        argv=list(map(str,argv))
        with (s/(label+'.stdout')).open('w') as out,(s/(label+'.stderr')).open('w') as err:
            r=subprocess.run(argv,cwd=cwd,env=env,stdout=out,stderr=err,timeout=55)
        commands.append(dict(command=argv,cwd=str(cwd),exit_code=r.returncode));save(s/'recovery-commands.json',commands)
        assert r.returncode==0,label
    def cli(label,*args):invoke(label,[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*args])
    invoke('restored-audit',[sys.executable,'-B','-m','fixtures.context_transactions.audit','--stage',evidence,'--output',s/'restored-verification.json'])
    assert (s/'restored-verification.json').read_bytes()==(evidence/'verification.json').read_bytes()
    for label,plan in (('healthy','plan'),('fault','fault-plan')):
        output=s/('restored-'+label+'-check.json')
        cli('restored-'+label+'-check','rdma-context-check','--plan',evidence/plan,'--run',evidence/label,'--output',output)
        assert output.read_bytes()==(evidence/(label+'-check.json')).read_bytes()
    # Independent one-put input, no reuse of either previous network execution.
    cfg=load(source/'examples/scaleout_context_transactions/program.json')
    cfg['communicators']=cfg['communicators'][:1];cfg['bindings']=[cfg['bindings'][1]]
    cfg['program']['operations']=[cfg['program']['operations'][1]]
    save(s/'fresh-input.json',cfg)
    cli('fresh-topology','topology','--config',source/'examples/scaleout_context_transactions/topology.json','--output',s/'fresh-topology')
    cli('fresh-plan','rdma-context-plan','--config',s/'fresh-input.json','--topology',s/'fresh-topology',
        '--protocol',source/'examples/scaleout_context_transactions/rdma.json','--output',s/'fresh-plan')
    cli('fresh-smoke','rdma-transaction-probe','--topology',s/'fresh-plan','--protocol',s/'fresh-plan/protocol.json',
        '--config',s/'fresh-plan/transactions.json','--backend',evidence/'runtime/run','--output',s/'fresh-smoke')
    cli('fresh-check','rdma-context-check','--plan',s/'fresh-plan','--run',s/'fresh-smoke','--output',s/'fresh-check.json')
    result=load(s/'fresh-smoke/result.json');report=audit(result,load(s/'fresh-plan/transactions.json'))
    libraries=[Path(p) for p in result['loaded_libraries'] if Path(p).name.startswith('libns3')]
    assert len(libraries)==8 and all(p.parent==evidence/'runtime/lib' and sha(p)==sha(s/'runtime/lib'/p.name) for p in libraries)
    assert sha(evidence/'runtime/transaction-probe')==sha(s/'runtime/transaction-probe')
    save(s/'recovery.json',dict(passed=True,restored_source_root=str(source),cwd=str(cwd),
        offline_reports_byte_equal=3,new_network_executions=1,old_network_reexecutions=0,
        actual_bundled_ns3_libraries=[str(p) for p in libraries],smoke=report,
        scope='ABI-bound bundled runtime, new Python root and empty cwd; not clean native build or cross-host test'))


if __name__=='__main__':main()
