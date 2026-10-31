"""Reproduce one published real plan from an empty cwd, without raw routes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
load=lambda p:json.loads(Path(p).read_bytes())


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--published',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();published=a.published.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    document=(published/'report.html').read_text();links=re.findall(r'href="([^"]+)"',document)
    assert links and all((published/link).is_file() for link in links)
    for path,digest in load(published/'receipt.json')['code_sha256'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest
    empty=out/'empty-cwd';empty.mkdir();commands=[]
    config=published/'inputs/modern-group-0/input.json'
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    for name,args in [('plan',['modern-deepep-plan','--format','compressed-json','--config',config,'--output',out/'plan']),
                      ('check',['modern-deepep-check','--plan',out/'plan','--output',out/'check.json'])]:
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,args)]
        r=subprocess.run(command,cwd=empty,env=env,capture_output=True,text=True,timeout=60)
        (out/(name+'-stdout.log')).write_text(r.stdout);(out/(name+'-stderr.log')).write_text(r.stderr)
        commands.append(dict(command=command,cwd=str(empty),exit_code=r.returncode));assert r.returncode==0
    expected=load(published/'inputs/modern-group-0/manifest.json');actual=load(out/'plan/manifest.json')
    assert actual==expected and load(out/'check.json')['all_completions_reachable']
    result=dict(passed=True,local_html_links=len(links),empty_cwd_public_commands=commands,
        published_manifest_identical=True,raw_routes_required=False,old_run_artifacts_required=False,new_network_runs=0)
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
