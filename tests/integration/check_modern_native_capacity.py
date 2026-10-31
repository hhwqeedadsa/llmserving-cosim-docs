"""Public CLI in an empty cwd; no native executable or network invocation."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[]
    cfg=json.loads((ROOT/'examples/modern_deepep_native/input.json').read_text())['modern']
    (out/'input.json').write_text(json.dumps(cfg,indent=2)+'\n')
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')

    def cli(name,source,target,code):
        args=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','modern-deepep-native-capacity',
              '--config',str(source),'--output',str(target)]
        r=subprocess.run(args,cwd=out,env=env,capture_output=True,text=True,timeout=30)
        commands.append(dict(command=args,cwd=str(out),exit_code=r.returncode))
        (out/(name+'-stdout.log')).write_text(r.stdout);(out/(name+'-stderr.log')).write_text(r.stderr)
        assert r.returncode==code,(name,r.returncode,r.stderr)
        return r

    for name in ('first','repeat'):
        cli(name,out/'input.json',out/(name+'.json'),0)
    assert (out/'first.json').read_bytes()==(out/'repeat.json').read_bytes()
    good=json.loads((out/'first.json').read_text())
    short=deepcopy(cfg);short['limits']['max_output_bytes']=1
    (out/'short.json').write_text(json.dumps(short))
    cli('short',out/'short.json',out/'rejected.json',1)
    bad=json.loads((out/'rejected.json').read_text())
    assert bad['failures']==['canonical_plan_bytes'] and not bad['network_executed']
    before=(out/'first.json').read_bytes()
    r=cli('overwrite',out/'input.json',out/'first.json',1)
    assert 'already exists' in r.stderr and (out/'first.json').read_bytes()==before
    (out/'invalid.json').write_text('[]')
    cli('invalid',out/'invalid.json',out/'invalid-report.json',1)
    assert not (out/'invalid-report.json').exists()
    assert good['passed'] and not good['runtime_admitted'] and not good['network_executed']
    receipt=dict(passed=True,commands=commands,repeat_bytes_identical=True,network_runs=0,
                 counts=good['counts'],negative_failures=bad['failures'],cwd_independent=True)
    (out/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
