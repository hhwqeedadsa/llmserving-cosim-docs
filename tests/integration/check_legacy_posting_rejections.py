"""Check full EP32 queue admission without starting a network or raising limits."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','execution','backend','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();root=a.inputs.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    load=lambda path:json.loads(path.read_bytes());base=load(a.execution);records=[]
    from moe_ub_cosim.legacy_native import minimum_posting_span
    assert minimum_posting_span(base)==2 and base['queue_depth']==32
    bad=deepcopy(base);bad['queue_depth']=1
    execution=out/'queue-one.json';execution.write_text(json.dumps(bad)+'\n')
    binding=load(root/'binding.json');binding['queue_depth']=1
    binding_path=out/'binding-one.json';binding_path.write_text(json.dumps(binding)+'\n')
    native_result=out/'native-result.json';public_out=out/'public'
    commands=[('native',[a.backend.resolve(),root/'topology/topology.json',root/'normal.json',execution,native_result],
               'queue depth cannot hold next publication span: need at least 2',native_result),
        ('public',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','deepep-run','--plan',root/'plan',
          '--binding',binding_path,'--topology',root/'topology','--protocol',root/'normal.json',
          '--transport',root/'transport.json','--backend',a.backend.resolve(),'--output',public_out],
               'cannot hold publication span=2',public_out/'execution-monitor')]
    for name,command,error,result in commands:
        command=list(map(str,command));run=subprocess.run(command,capture_output=True,text=True,timeout=30)
        (out/(name+'-stdout.log')).write_text(run.stdout);(out/(name+'-stderr.log')).write_text(run.stderr)
        records.append(dict(command=command,exit_code=run.returncode,expected_error=error))
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        assert run.returncode!=0 and error in run.stderr and not result.exists(),name
    summary=dict(passed=True,mandatory_posting_span=2,configured_depth=32,
                 rejected_depth=1,public_rejections=1,native_rejections=1,
                 valid_network_executed=False,limits_increased=False)
    (out/'verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
