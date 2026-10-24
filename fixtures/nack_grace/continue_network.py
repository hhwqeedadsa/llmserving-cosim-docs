"""Remaining slots after the preserved idle-log failure; no successful reruns."""
import argparse
from pathlib import Path
import sys
from stage import run,load,save,sha
from run import normalized


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();build=load(s/'build.json');cases=load(s/'cases.json')
    assert sha(s/'bin/stream-probe')==build['binaries']['stream-probe']
    records=load(s/'commands.json');assert sum(r['network'] for r in records)==3
    assert next(r for r in records if r['label']=='head-loss')['exit_code']!=0
    comparison=normalized(load(s/'network/default-old/result.json'),load(s/'network/default-new/result.json'),build['libraries'])
    for name,label in [('head-loss','head-loss-idle-fixed'),('no-feedback','no-feedback')]:
        x=cases[name]
        run(repo,s,label,[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-stream-probe',
            '--topology',s/'topology','--protocol',s/'inputs'/x['protocol'],'--config',s/'inputs'/x['work'],
            '--backend',s/'bin/stream-probe','--output',s/'network'/label],network=True)
    cases['head-loss-idle-fixed']=cases['head-loss'];save(s/'cases.json',cases)
    save(s/'run.json',dict(passed=True,actual_network_attempts=5,successful_networks=4,
        sixth_reserved='fresh-mixed',default=comparison,default_program='v1; final idle-only grace logging repair does not change omitted path',
        failure='head-loss-v1 SIGSEGV on closing a declared idle stream; source repaired, original saved'))

if __name__=='__main__':main()
