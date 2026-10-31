"""New-directory public workflow; compare optional pre-change flat baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zlib

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline',type=Path);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    commands=[]
    def cli(name,args):
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,args)]
        r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
        (out/(name+'-stdout.log')).write_text(r.stdout);(out/(name+'-stderr.log')).write_text(r.stderr)
        commands.append(dict(command=command,cwd=str(ROOT),exit_code=r.returncode));assert r.returncode==0
    for name in ('flat','compressed'):
        args=['modern-deepep-plan','--config',ROOT/'examples/modern_deepep_partial_order/input.json','--output',out/name]
        if name=='compressed':args+=['--format','compressed-json']
        cli('plan-'+name,args)
        for label,extra in [('all',[]),('withheld',['--withhold','m0.dispatch.expert_count.s2.d1.visible'])]:
            cli(name+'-'+label,['modern-deepep-check','--plan',out/name,'--output',out/(name+'-'+label+'.json'),*extra])
    for label in ('all','withheld'):assert (out/('flat-'+label+'.json')).read_bytes()==(out/('compressed-'+label+'.json')).read_bytes()
    plain=(out/'flat/plan.json').read_bytes();stored=(out/'compressed/plan.json.zlib').read_bytes()
    assert zlib.decompress(stored)==plain
    baseline_equal=None
    if a.baseline:
        baseline_equal=all((a.baseline/name).read_bytes()==(out/'flat'/name).read_bytes() for name in ('input.json','plan.json','manifest.json'))
        assert baseline_equal
    receipt=dict(passed=True,public_cli_commands=commands,canonical_bytes=len(plain),stored_bytes=len(stored),
        canonical_sha256=hashlib.sha256(plain).hexdigest(),old_default_bytes_identical=baseline_equal,
        both_closures_identical=True,new_network_runs=0,hardware_calibrated=False)
    (out/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
