"""Public modern two-call plan checks for the same recorded source selections."""
import argparse
from copy import deepcopy
from pathlib import Path

from study import ROOT,cli,load,save,sha

if not __debug__:raise RuntimeError('Do not run with -O')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--admission',type=Path,required=True)
    p.add_argument('--storage',choices=('flat','compressed-json'),default='flat')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    records=[]
    for group in (0,1):
        folder=out/f'group-{group}';folder.mkdir()
        original=load(a.admission/f'group-{group}'/'input.json')
        modern=load(ROOT/'examples/modern_deepep_partial_order/input.json')
        modern['model']=dict(experts=256,top_k=8,hidden_size=7168,precision='bf16')
        modern['execution'].update(ranks=32,max_tokens_per_rank=1,connections=1,local_devices=[[0] for _ in range(32)])
        modern['calls']=deepcopy(original['calls'])
        modern['limits']=dict(max_events=65536,max_operations=16384,max_input_bytes=1048576,max_output_bytes=16777216)
        save(folder/'input.json',modern)
        r=cli(out,f'plan-{group}',['modern-deepep-plan','--config',folder/'input.json','--output',folder/'plan',
            '--format',a.storage],expect_success=False)
        record=dict(group=group,plan_accepted=r.returncode==0,exit_code=r.returncode,input_sha256=sha(folder/'input.json'),
            same_choices_as_legacy=modern['calls']==original['calls'],network_executed=False,storage=a.storage)
        if r.returncode==0:
            cli(out,f'check-{group}',['modern-deepep-check','--plan',folder/'plan','--output',folder/'check.json'])
            record.update(manifest=load(folder/'plan/manifest.json'),check=load(folder/'check.json'))
        else:
            assert 'budget' in r.stderr or 'too large' in r.stderr or 'exceed' in r.stderr
            record['rejection']=r.stderr.strip()
        records.append(record)
    save(out/'summary.json',dict(passed=True,groups=records,new_network_runs=0,hardware_calibrated=False,
        scope='Two independent untimed representations; not shared-network execution, runtime slot/callback acceptance or latency'))
    print((out/'summary.json').read_text())


if __name__=='__main__':main()
