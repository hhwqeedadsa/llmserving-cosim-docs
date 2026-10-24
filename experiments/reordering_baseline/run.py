"""Three new classical-grace networks through the public platform commands."""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys
from experiments.reordering_baseline.stage import run,load,save


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','backend','build-identity'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();obs=s/'inputs/observed';idx=load(obs/'index.json');build=load(a.build_identity)
    subprocess.run(['git','diff','--exit-code',idx['platform_commit'],'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    assert all(sha(s/'inputs'/name)==h for name,h in load(s/'inputs/provenance.json')['files'].items())
    assert sha(a.backend)==build['binaries']['stream-probe'] and all(sha(repo/name)==h for name,h in build['sources'].items())
    assert len(build['libraries'])==8 and all(sha(name)==h for name,h in build['libraries'].items())
    save(s/'runtime.json',dict(platform_commit=idx['platform_commit'],backend=str(a.backend),sha256=sha(a.backend),build_identity=build,
        libraries=build['libraries'],scope='read-only current program and eight fixed libraries; no fixture invocation'))
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout'];(s/'planned').mkdir();(s/'network').mkdir()
    run(repo,s,'topology',[*cli,'topology','--config',obs/'topology.json','--output',s/'topology'])
    assert (s/'topology/topology.json').read_bytes()==(s/'inputs/baseline/topology.json').read_bytes()
    for name in idx['cases']:
        run(repo,s,name+'-plan',[*cli,'rdma-causal-plan','--config',obs/idx['plan'],'--topology',s/'topology',
            '--protocol',obs/(name+'-protocol.json'),'--output',s/'planned'/name])
        w=load(s/'planned'/name/'work.json');old=load(s/'inputs/baseline'/name/'work.json')
        expected=dict(mode='nack_grace/1',wait_ns=16000,max_events=30000)
        assert w['transport_recovery'].pop('reordering')==expected and w==old
        before=load(s/'inputs/baseline'/name/'mapping.json');after=load(s/'planned'/name/'mapping.json')
        assert before['protocol_sha256']==after['protocol_sha256']
        assert before.pop('config_sha256')!=after.pop('config_sha256') and before==after
    for name in idx['cases']:
        run(repo,s,name+'-network',[*cli,'rdma-stream-probe','--topology',s/'topology','--protocol',obs/(name+'-protocol.json'),
            '--config',s/'planned'/name/'work.json','--backend',a.backend,'--output',s/'network'/name],True)
        r=load(s/'network'/name/'result.json');assert r['passed'] and r['packet_capture']['complete']
    assert sha(a.backend)==build['binaries']['stream-probe'] and all(sha(name)==h for name,h in build['libraries'].items())
    save(s/'run.json',dict(passed=True,new_actual_networks=3,saved_cases=3,phase_closed=True,platform_commit=idx['platform_commit'],
        work_equal_except_explicit_reordering=True,mapping_equal_except_config_hash=True,successful_network_reruns=0))

if __name__=='__main__':main()
