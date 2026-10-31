"""No-network delivery checks, including public CLI from an empty cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
from moe_ub_cosim.transaction_profile_build import _recorded_sources,RECOMPILED

ROOT=Path(__file__).resolve().parents[2]

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);a=p.parse_args();run=a.run.resolve();out=run/'delivery';out.mkdir(exist_ok=False)
    build=run/'build-v2';inputs=json.loads((build/'build-inputs.json').read_text())
    runtime=Path(inputs['runtime']);old=json.loads((runtime/'provenance.json').read_text())
    identity=json.loads((ROOT/'runs/legacy_shared_20261031/build-v2/build-inputs.json').read_text())
    assert _recorded_sources(old,identity)==inputs['original_sources']
    assert all(digest(ROOT/name)==wanted for name,wanted in inputs['sources'].items())
    assert all(digest(ROOT/name)==wanted for name,wanted in inputs['original_sources'].items() if name not in RECOMPILED)
    assert all(digest(build/'runtime/lib'/name)==wanted for name,wanted in inputs['library_hashes'].items())
    assert digest(build/'runtime/lib/libmoe-rdma-fabric.a')==inputs['fabric_archive_sha256']
    make=runpy.run_path(str(ROOT/'tests/integration/make_transaction_scaleout_input.py'))['make']
    assert make()==json.loads((ROOT/'examples/scaleout_rdma_transactions/scaleout-profile.json').read_text())
    empty=out/'empty-cwd';empty.mkdir();env=dict(os.environ,PYTHONPATH=str(ROOT/'python'));commands=[]
    def call(command,code=0,cwd=empty):
        r=subprocess.run(command,cwd=cwd,env=env,capture_output=True,text=True,timeout=120)
        commands.append(dict(command=command,cwd=str(cwd),exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n');assert r.returncode==code
    command=['python3','-B','-m','moe_ub_cosim','scaleout','rdma-preflight','--topology',str(run/'topology'),
        '--protocol',str(ROOT/'examples/scaleout_rdma_transactions/scaleout-rdma.json'),'--transactions',
        str(ROOT/'examples/scaleout_rdma_transactions/scaleout-profile.json'),'--output',str(out/'preflight.json')]
    call(command)
    bad=json.loads((ROOT/'examples/scaleout_rdma_transactions/scaleout-profile.json').read_text());bad['execution_limits']['profile']='unknown'
    (out/'bad.json').write_text(json.dumps(bad));command[command.index('--transactions')+1]=str(out/'bad.json')
    command[-1]=str(out/'rejected-preflight.json');call(command,code=1)
    call(['bash',str(ROOT/'scripts/check_deps.sh')],cwd=ROOT)
    links=[]
    for match in re.finditer(r'href="([^"#]+)(?:#[^"]*)?"',(ROOT/'fixtures/transaction_scaleout_profile/report.html').read_text()):
        path=ROOT/'fixtures/transaction_scaleout_profile'/match[1]
        if path.name!='verification.json':assert path.is_file(),str(path)
        links.append(match[1])
    result=dict(passed=True,checks='fixed sources/libraries/archive; generator equality; empty-cwd CLI admission/rejection; 13 locked dependencies; static HTML links',
                commands=commands,html_links=links,ns3_builds=0,new_networks=0,browser_rendered=False)
    (out/'verification.json').write_text(json.dumps(result,indent=2)+'\n');print('delivery checks passed')


if __name__=='__main__':main()
