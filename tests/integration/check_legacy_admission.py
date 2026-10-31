"""Reject impossible legacy inputs before creating a Fabric; no valid network run."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--execution',type=Path,required=True)
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    root=a.inputs.resolve();out=a.output.resolve();backend=a.backend.resolve()
    work=json.loads(a.execution.read_bytes());rows=[]

    def rejected(name,command,expected,result):
        run=subprocess.run(list(map(str,command)),capture_output=True,text=True,timeout=30)
        (out/(name+'-stdout.log')).write_text(run.stdout)
        (out/(name+'-stderr.log')).write_text(run.stderr)
        rows.append(dict(name=name,command=list(map(str,command)),exit_code=run.returncode,
                         expected_error=expected,result_created=result.exists()))
        (out/'commands.json').write_text(json.dumps(rows,indent=2)+'\n')
        assert run.returncode!=0 and expected in run.stderr and not result.exists(),name

    rejected('public-capacity',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','deepep-run',
        '--plan',root/'plan','--binding',root/'binding.json','--topology',root/'topology',
        '--protocol',root/'normal.json','--transport',root/'transport.json','--backend',backend,
        '--output',out/'public-capacity'], 'mandatory source sends=32464',
        out/'public-capacity/execution-monitor')
    mutations=[('capacity',None,'mandatory source sends'),
        ('ranks',lambda w:w.update(ranks=33),'integer outside'),
        ('mapping',lambda w:w['rank_to_gpu'].__setitem__(1,w['rank_to_gpu'][0]),'mapping absent or repeated'),
        ('layout',lambda w:w.update(dispatch_bytes=w['dispatch_bytes']+1),'BF16 layout'),
        ('queue',lambda w:w.update(queue_depth=3),'power of two'),
        ('stage',lambda w:w['operations'][0][10].__setitem__(0,w['operations'][0][10][1]),'stage kind mismatch'),
        ('expert',lambda w:w['operations'][0].__setitem__(6,256),'integer outside'),
        ('work-budget',lambda w:w['limits'].update(work=w['limits']['work']-1),'work budget mismatch')]
    for name,mutate,error in mutations:
        bad=deepcopy(work)
        if mutate:mutate(bad)
        path=out/(name+'.json');path.write_text(json.dumps(bad)+'\n');result=out/(name+'-result.json')
        rejected('native-'+name,[backend,root/'topology/topology.json',root/'normal.json',path,result],error,result)
    summary=dict(passed=True,public_rejections=1,native_rejections=len(mutations),
                 valid_network_executed=False,limits_increased=False)
    (out/'verification.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
