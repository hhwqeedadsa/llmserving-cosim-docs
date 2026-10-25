"""Run each preregistered condition at most once using public CLI."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
from experiments.extreme_fault_stress.stage import load, save, sha, run


def identity(repo, backend):
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip()=='68a07f7d5e53bbe97d275af41d4964ad01b864f3'
    subprocess.run(['git','diff','--exit-code','e8533c9','--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    listing=subprocess.check_output(['ldd',str(backend)],text=True)
    libs={m:sha(m) for m in re.findall(r'=> (\S+/libns3[^\s]+)',listing)}
    assert len(libs)==8,libs
    return dict(backend=str(backend),backend_sha256=sha(backend),libraries=libs,
                platform_reference='e8533c9',native_commit='68a07f7d5e53bbe97d275af41d4964ad01b864f3')


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage','backend'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--inputs',type=Path)
    p.add_argument('--network-only',action='store_true',help='Continue only not-started networks after a preparation-only failure')
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();inputs=(a.inputs or s/'inputs').resolve();obs=inputs/'observed';idx=load(inputs/'manifest.json')
    assert all(sha(inputs/n)==h for n,h in idx['files'].items())
    before=identity(repo,a.backend.resolve());save(s/'runtime.json',before)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    top=s/('topology-'+inputs.name)
    if not a.network_only:
        run(repo,s,'topology-'+inputs.name,cli+['topology','--config',obs/'topology.json','--output',top])
    else:
        assert load(s/('topology-'+inputs.name+'-monitor/probe.json'))['status']=='completed'
        assert not any(x.get('native_network_started') for x in load(s/'commands.json'))
    topology=load(top/'topology.json');edges={tuple(sorted((x['src'],x['dst']))) for x in topology['links']}
    from collections import deque
    connectivity={}
    for case in idx['cases']:
        removed={tuple(sorted((a,b))) for a,b,_ in case['failures']};assert removed<=edges
        reached={0};q=deque([0])
        while q:
            u=q.popleft()
            for x,y in edges-removed:
                v=y if x==u else x if y==u else None
                if v is not None and v not in reached:reached.add(v);q.append(v)
        connectivity[case['name']]=dict(reachable_from_0=sorted(reached),all_endpoints_reachable=set(range(8))<=reached)
        assert connectivity[case['name']]['all_endpoints_reachable']==case['expected_complete']
    save(s/'connectivity.json',connectivity);(s/'network').mkdir(exist_ok=True)
    for case in idx['cases']:
        name=case['name']; output=s/'network'/name
        report=run(repo,s,name+'-'+inputs.name,cli+['rdma-stream-probe','--topology',top,
            '--protocol',obs/(name+'-protocol.json'),'--config',obs/(case['load']+'-work.json'),
            '--backend',a.backend,'--output',output],network=True,allow_failure=not case['expected_complete'])
        assert (output/'result.json').is_file(),name+': missing partial result; stop'
        r=load(output/'result.json');assert r['native_network_executed']
        assert r['packet_capture']['complete'] and not r['packet_capture']['omitted_events'],name+': capture incomplete; stop'
        assert {n for n in r['loaded_libraries'] if Path(n).name.startswith('libns3.')}==set(before['libraries'])
        if case['expected_complete']:assert r['passed'] and r['completed_work']==90,name+': unexpected transport failure; stop'
        else:assert not r['passed'] and r['persistent_transport']['recovery']['failed'],name+': expected isolated retry failure'
        save(s/(name+'-receipt.json'),dict(passed=bool(r['passed']),cli_status=report['status'],completed_work=r['completed_work']))
        print(name,'completed_work',r['completed_work'],'finish_ns',r['finish_ns'],flush=True)
    assert identity(repo,a.backend.resolve())==before
    save(s/'run.json',dict(phase_closed=True,new_networks=6,successful_network_reruns=0,identity_unchanged=True))


if __name__=='__main__':main()
