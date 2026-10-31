"""Compare functional controller/layout bytes with an explicit old checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]
PROGRAM="""
import json
from modern_native_test import example, expert_matrix, declared_callback_drive
rows=[]
for cfg in (example(),expert_matrix()):
    c=declared_callback_drive(cfg)
    rows.append(dict(initial=c.layout.initial,bindings=c.layout.bindings,
        plan=c.layout.plan,description=c.layout.description,snapshot=c.snapshot()))
print(json.dumps(rows,sort_keys=True,separators=(',',':')))
"""


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline-commit',required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    commit=subprocess.check_output(['git','rev-parse',a.baseline_commit+'^{commit}'],cwd=ROOT,text=True).strip()
    archive=out/'baseline.tar'
    with archive.open('wb') as f:
        subprocess.run(['git','archive',commit,'python'],cwd=ROOT,stdout=f,check=True,timeout=30)
    baseline=out/'baseline';baseline.mkdir()
    with tarfile.open(archive) as t:
        for entry in t.getmembers():
            assert (entry.name=='python' and entry.isdir() or entry.name.startswith('python/'))
            assert not entry.issym() and not entry.islnk()
            assert (baseline/entry.name).resolve().is_relative_to(baseline)
        t.extractall(baseline)
    commands=[]
    for name,package in (('old',baseline/'python'),('current',ROOT/'python')):
        env=dict(os.environ,PYTHONPATH=str(package)+':'+str(ROOT/'tests/unit'),PYTHONDONTWRITEBYTECODE='1')
        command=[sys.executable,'-B','-c',PROGRAM]
        r=subprocess.run(command,cwd=out,env=env,capture_output=True,timeout=30)
        (out/(name+'.json')).write_bytes(r.stdout);(out/(name+'-stderr.log')).write_bytes(r.stderr)
        commands.append(dict(command=command,cwd=str(out),pythonpath=env['PYTHONPATH'],exit_code=r.returncode))
        assert r.returncode==0,r.stderr
    assert (out/'old.json').read_bytes()==(out/'current.json').read_bytes()
    result=dict(passed=True,baseline_commit=commit,commands=commands,byte_identical=True,
        compared='Two complete declared functional controller transcripts, native initial state, plan, bindings and layout; no actual network',
        bytes=(out/'current.json').stat().st_size,sha256=hashlib.sha256((out/'current.json').read_bytes()).hexdigest(),network_runs=0)
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
