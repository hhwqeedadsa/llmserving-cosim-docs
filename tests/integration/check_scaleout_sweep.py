"""Public sweep: expected failure continues, unexpected failure remains nonzero."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--audit-existing',action='store_true',help='Check retained actual outputs without rerunning simulations')
    a=p.parse_args();out=a.output.resolve()
    if a.audit_existing:
        assert out.is_dir();commands=read(out/'commands.json')
    else:out.mkdir(exist_ok=False);commands=[]
    original=read(ROOT/'examples/scaleout_rdma_resilience/sweep.json')
    for kind,name in original['base'].items():
        original['base'][kind]=str((ROOT/'examples/scaleout_rdma_resilience'/name).resolve())
    failure=dict(name='known-failure',expect='recovery_retry_exhausted',comparison_group='batch4',patches=dict(protocol=dict(
        recovery=dict(initial_rto_ns=40000,max_rto_ns=160000,max_retries=2,history=None),
        admission=dict(per_destination=None),faults=[dict(node=0,port=0,message_id=1,kind='ack',sequence=6144,
        count=10000,start_ns=0,end_ns=5000000)])))
    original['cases']=[dict(name='batch1',patches=dict(service=dict(workload=dict(batch_per_rank=1)))),
                       failure,dict(name='success-after-failure',patches={},comparison_group='batch4')]
    def execute(name,spec,success):
        argv=[sys.executable,'-m','moe_ub_cosim','scaleout','sweep','--config',str(out/(name+'.json')),
              '--backend',str(a.backend),'--protoc',a.protoc,'--output',str(out/name)]
        if a.audit_existing:
            assert read(out/(name+'.json'))==spec
            prior=next(c for c in commands if c['argv']==argv)
            assert (prior['exit_code']==0)==success
            return
        save(out/(name+'.json'),spec)
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            completed=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=330)
        commands.append(dict(argv=argv,cwd=str(ROOT),exit_code=completed.returncode,expected_success=success))
        save(out/'commands.json',commands);assert (completed.returncode==0)==success
    execute('expected',original,True)
    result=read(out/'expected/summary.json')
    assert result['matches_expectations'] and [c['passed'] for c in result['cases']]==[True,False,True]
    assert result['cases'][1]['frontend_complete_ns'] is None
    assert result['cases'][1]['failure_reason']=='recovery_retry_exhausted'
    assert (out/'expected/summary.csv').is_file()
    unexpected=deepcopy(original);unexpected['cases']=[dict(failure,expect='success')]
    execute('unexpected',unexpected,False)
    result=read(out/'unexpected/summary.json')
    assert result['state']=='unexpected_failure' and not result['matches_expectations'] and not result['cases'][0]['passed']
    bounded=deepcopy(original);bounded['budget']['wall_seconds']=1
    bounded['cases']=[dict(name=f'budget-{i:02}',patches=dict(protocol=dict(limits=dict(wall_seconds=1)))) for i in range(32)]
    execute('budget-stop',bounded,False)
    result=read(out/'budget-stop/summary.json');resource=read(out/'budget-stop/execution/probe.json')
    assert result['state'] in ('execution_stopped','unexpected_failure') and not result['matches_expectations']
    assert resource['status']=='wall_limit'
    save(out/'summary.json',dict(passed=True,expected_failure_retained=True,continued_after_expected_failure=True,
         unexpected_failure_nonzero=True,aggregate_budget_stop_terminal=True,expected_sweep_cases=3,unexpected_sweep_cases=1,
         verification_mode='audit_existing_actual_outputs' if a.audit_existing else 'executed_and_checked'))
    print('Public sweep success/failure semantics and continuation verified')


if __name__=='__main__':main()
