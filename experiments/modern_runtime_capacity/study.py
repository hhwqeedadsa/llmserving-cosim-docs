"""Read the two fixed complete configurations through the public capacity CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')


def main():
    if not __debug__:raise RuntimeError('Do not use -O')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    frozen=dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        network_object='128-endpoint Clos, previous two EP32 deployments; not generated or executed here',
        inputs=[],new_network_runs=0,script_sha256=sha(Path(__file__)))
    sources=[]
    for group in (0,1):
        src=ROOT/f'experiments/route_window_network/results/20261031/inputs/modern-group-{group}/input.json'
        folder=out/f'group-{group}';folder.mkdir();shutil.copyfile(src,folder/'input.json')
        frozen['inputs'].append(dict(group=group,path=str(src.relative_to(ROOT)),sha256=sha(src)))
        sources.append(folder)
    save(out/'frozen.json',frozen)
    records=[]
    for group,folder in enumerate(sources):
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','modern-deepep-native-capacity',
                 '--config',str(folder/'input.json'),'--output',str(folder/'capacity.json')]
        env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
        r=subprocess.run(command,cwd=out,env=env,capture_output=True,text=True,timeout=60)
        save(folder/'command.json',dict(command=command,cwd=str(out),exit_code=r.returncode))
        (folder/'stdout.log').write_text(r.stdout);(folder/'stderr.log').write_text(r.stderr)
        assert r.returncode in (0,1) and (folder/'capacity.json').exists(),r.stderr
        report=json.loads((folder/'capacity.json').read_text())
        assert r.returncode==int(not report['passed']) and not report['network_executed'] and not report['runtime_admitted']
        records.append(dict(group=group,exit_code=r.returncode,report_sha256=sha(folder/'capacity.json'),
            static_checks_passed=report['passed'],counts=report['counts'],checks=report['checks'],
            unverified=report['unverified'],failures=report['failures']))
    summary=dict(passed=True,groups=records,new_network_runs=0,hardware_calibrated=False,
        scope='Complete modern static requirements for the fixed real selections; not native deployment admission or latency')
    save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
