"""Sixth and final network: mixed failure from a copied public Python root and empty cwd."""
import argparse
from pathlib import Path
import shutil
import sys
from stage import load,save,sha,run


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve()
    assert sum(r['network'] for r in load(s/'commands.json'))==5
    root=s/'fresh-source';root.mkdir();empty=s/'empty-cwd';empty.mkdir()
    shutil.copytree(repo/'python',root/'python',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(repo/'examples/scaleout_nack_grace',root/'examples/scaleout_nack_grace')
    hashes={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}
    save(s/'fresh-source-manifest.json',hashes)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout'];environment=dict(PYTHONPATH=str(root/'python'))
    source=root/'examples/scaleout_nack_grace'
    run(repo,s,'fresh-topology',[*cli,'topology','--config',source/'topology.json','--output',s/'fresh-topology'],cwd=empty,env=environment)
    assert (s/'topology/topology.json').read_bytes()==(s/'fresh-topology/topology.json').read_bytes()
    run(repo,s,'fresh-mixed',[*cli,'rdma-stream-probe','--topology',s/'fresh-topology',
        '--protocol',source/'protocol.json','--config',source/'work.json',
        '--backend',s/'bin/stream-probe','--output',s/'network/fresh-mixed'],cwd=empty,network=True,env=environment)
    cases=load(s/'cases.json');cases['fresh-mixed']=cases['mixed'];save(s/'cases.json',cases)
    assert all(sha(root/p)==h==sha(repo/p) for p,h in hashes.items())
    save(s/'fresh.json',dict(passed=True,root=str(root),empty_cwd=str(empty),copied_files=len(hashes),
        actual_network_attempts=6,successful_networks=5,closed=True,
        runtime_scope='copied Python and example inputs; fixed external native program and libraries, not a clean build'))

if __name__=='__main__':main()
