"""Three fixed complete-application networks through public commands."""
import argparse
from pathlib import Path
import subprocess
import sys
from experiments.nic_wakeup_effect.stage import run, load, save, sha


def identity(repo, runtime):
    b = runtime['build_identity']; native = repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert sha(runtime['backend']) == runtime['sha256'] == b['binaries']['bin/stream-probe']
    assert len(b['libraries']) == 8 and all(sha(n) == h for n,h in b['libraries'].items())
    assert all(sha(n) == h for n,h in b['inputs'].items())
    assert all(sha(repo/n) == h for n,h in b['platform_sources'].items())
    assert all(sha(native/n) == h for n,h in b['native_sources'].items())
    subprocess.run(['git', 'diff', '--exit-code', runtime['platform_commit'], '--', 'python', 'backend', 'deps.lock.json', '.gitmodules'], cwd=repo, check=True)
    assert subprocess.check_output(['git','rev-parse','HEAD'], cwd=native,text=True).strip() == '68a07f7d5e53bbe97d275af41d4964ad01b864f3'
    return dict(passed=True, backend_sha256=runtime['sha256'], libraries=8,
        platform_and_native_sources_and_build_inputs_unchanged=True, network_executions=0)


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'stage', 'backend', 'build-identity'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); s = a.stage.resolve(); obs = s/'inputs/observed'; idx = load(obs/'index.json')
    assert all(sha(s/'inputs'/n)==h for n,h in load(s/'inputs/provenance.json')['files'].items())
    runtime = dict(platform_commit=idx['platform_commit'],backend=str(a.backend.resolve()),sha256=sha(a.backend),build_identity=load(a.build_identity))
    identity(repo,runtime); save(s/'runtime.json',runtime)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout'];(s/'planned').mkdir();(s/'network').mkdir()
    run(repo,s,'topology',[*cli,'topology','--config',obs/'topology.json','--output',s/'topology'])
    assert (s/'topology/topology.json').read_bytes()==(s/'inputs/baseline/topology.json').read_bytes()
    for case in idx['cases']:
        run(repo,s,case+'-plan',[*cli,'rdma-causal-plan','--config',obs/idx['plan'],'--topology',s/'topology',
            '--protocol',obs/(case+'-protocol.json'),'--output',s/'planned'/case])
        for name in ('work.json','mapping.json'):
            assert (s/'planned'/case/name).read_bytes()==(s/'inputs/baseline'/case/name).read_bytes(), name+': input changed'
    for case in idx['cases']:
        run(repo,s,case+'-network',[*cli,'rdma-stream-probe','--topology',s/'topology','--protocol',obs/(case+'-protocol.json'),
            '--config',s/'planned'/case/'work.json','--backend',a.backend,'--output',s/'network'/case],True)
        r=load(s/'network'/case/'result.json');assert r['passed'] and r['packet_capture']['complete']
        assert set(r['loaded_libraries'])==set(runtime['build_identity']['libraries'])
    save(s/'identity.json',identity(repo,runtime))
    save(s/'run.json',dict(passed=True,new_actual_networks=3,immutable_saved_baselines=3,phase_closed=True,
        planned_work_mapping_and_topology_byte_identical=True,successful_network_reruns=0))


if __name__=='__main__':main()
