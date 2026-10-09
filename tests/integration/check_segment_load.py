"""Independent C++ finite-scope validation, including altered initial state."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

from moe_ub_cosim.user_io import read,save


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--loader',type=Path,required=True)
    p.add_argument('--graph',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False);plan=read(a.graph/'plan.json');commands=[]
    assert plan['schema_version']=='moe-ub-model-segment-plan/1'
    for label in ('valid','initial-time','original-layer','context','scope-schema','work'):
        path=out/label;shutil.copytree(a.graph,path);changed=deepcopy(plan)
        if label=='initial-time':changed['segment']['initial_state']['simulation_ns']=1
        elif label=='original-layer':changed['segment']['first_layer']-=1
        elif label=='context':changed['segment']['requests'][0]['token_index']+=1
        elif label=='scope-schema':changed['schema_version']='moe-ub-full-model-plan/1'
        elif label=='work':changed['rank_graphs'][0]['nodes'][0]['duration_ns']+=1
        save(path/'plan.json',changed);cmd=[str(a.loader.resolve()),str(path),'--compare-retention']
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        for stream in ('stdout','stderr'):(out/f'{label}.{stream}.log').write_text(getattr(result,stream))
        commands.append(dict(command=cmd,cwd=str(Path.cwd()),exit_code=result.returncode))
        save(out/'commands.json',commands);assert result.returncode==(0 if label=='valid' else 1),(label,result.stderr)
    save(out/'summary.json',dict(passed=True,valid=1,rejected=5))
    print('PASS: C++ segment reconstruction, retention, and five altered scope/work rejections')


if __name__=='__main__':main()
