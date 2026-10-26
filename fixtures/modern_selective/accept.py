"""Bounded public-CLI integration checks; no research performance claim."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def load(p): return json.loads(p.read_text())
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','stage','runtime','identity','four-input','four-manifest'):
        p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    assert not stage.exists(),'new stage required; never overwrite or retry a failed network'
    stage.mkdir();(stage/'inputs').mkdir()
    identity=load(a.identity)
    def check_identity():
        assert all(sha(a.runtime/n)==h for n,h in identity['files'].items())
        assert all(sha(repo/n)==h for n,h in identity['sources'].items())
    check_identity();save(stage/'runtime-identity.json',identity)
    manifest=load(a.four_manifest)
    assert sha(a.four_input)==manifest['inputs/four-routes.json']
    save(stage/'source.json',dict(four_input=str(a.four_input),four_sha256=sha(a.four_input),
         runtime=str(a.runtime),runtime_identity_sha256=sha(a.identity),repository_head=subprocess.check_output(
             ['git','rev-parse','HEAD'],cwd=repo,text=True).strip()))
    records=[];elapsed=0;networks=0
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    def run(label,argv,network=False,cwd=None):
        nonlocal elapsed,networks
        assert elapsed+65<900
        assert sum(f.stat().st_size for f in stage.rglob('*') if f.is_file())+2*2**20<512*2**20
        assert shutil.disk_usage(stage).free>8*2**30 and shutil.disk_usage(repo).free>64*2**20
        assert not network or networks<4
        cwd=repo if cwd is None else cwd
        records.append(dict(label=label,command=list(map(str,argv)),cwd=str(cwd),network=network,state='started'))
        save(stage/'commands.json',records);networks+=int(network)
        monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(128*2**20),
            '--artifact-root',str(stage),'--wall-seconds','60','--min-free-bytes',str(8*2**30),'--',*map(str,argv)]
        t=time.monotonic();r=subprocess.run(monitor,cwd=cwd,env=env,timeout=65)
        elapsed+=time.monotonic()-t;records[-1].update(state='terminal',exit_code=r.returncode)
        save(stage/'commands.json',records)
        assert r.returncode==0,'preserve failure and stop; no automatic retry or budget increase'
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    run('units',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','modern_native_test.py'])
    run('regressions',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','scaleout_transactions_test.py'])
    run('stream-regressions',[sys.executable,'-B','-m','unittest','discover','-s','tests/unit','-p','scaleout_streams_test.py'])
    run('topology',[*cli,'topology','--config',repo/'examples/modern_deepep_native/topology.json','--output',stage/'topology'])
    # Execute old source only for exact default-layout comparison, not a network.
    from moe_ub_cosim.modern_native_layout import ModernNativeLayout
    old_source=subprocess.check_output(['git','show','aeaf4aa:python/moe_ub_cosim/modern_native_layout.py'],cwd=repo)
    namespace={'__name__':'moe_ub_cosim._baseline_layout','__package__':'moe_ub_cosim'}
    exec(compile(old_source,'baseline-modern-native-layout','exec'),namespace)
    protocol=load(repo/'examples/modern_deepep_native/rdma.json');topology=load(stage/'topology/topology.json')
    defaults=[load(repo/'examples/modern_deepep_native/input.json'),load(a.four_input)]
    for cfg in defaults:
        old=namespace['ModernNativeLayout'](topology,protocol,cfg);new=ModernNativeLayout(topology,protocol,cfg)
        assert old.initial==new.initial and old.plan==new.plan and old.connections==new.connections
    save(stage/'default-equivalence.json',dict(passed=True,inputs=2,whole_initial_plan_connections_equal=True,
         old_source_sha256=hashlib.sha256(old_source).hexdigest(),network_executions=0))
    selective=load(repo/'examples/modern_deepep_native/input-selective.json')['provider']
    for name,cfg in zip(('small','four'),defaults):
        cfg['provider']['transport_recovery']=deepcopy(selective['transport_recovery'])
        cfg['provider']['dcqcn_reference']=deepcopy(selective['dcqcn_reference'])
        save(stage/'inputs'/(name+'.json'),cfg)
        layout=ModernNativeLayout(topology,protocol,cfg)
        first=next(c for c in layout.connections if c['src']!=c['dst'])
        for suffix in ('healthy','fault'):
            proto=deepcopy(protocol)
            if suffix=='fault':proto['faults']=[dict(node=first['dst'],port=first['destination_nic'],
                 message_id=2*first['id'],kind='data',sequence=0,start_ns=0,end_ns=1000000,count=1)]
            save(stage/'inputs'/(name+'-'+suffix+'-protocol.json'),proto)
    # First run is an independent public example from a copied Python source root.
    fresh=stage/'fresh-source';fresh.mkdir();shutil.copytree(repo/'python/moe_ub_cosim',fresh/'moe_ub_cosim')
    empty=stage/'empty-cwd';empty.mkdir()
    env['PYTHONPATH']=str(fresh)+':'+str(repo)
    for name in ('small-healthy','small-fault','four-healthy','four-fault'):
        family=name.split('-')[0]
        run(name,[*cli,'modern-deepep-native','--topology',stage/'topology','--protocol',stage/'inputs'/(name+'-protocol.json'),
            '--config',stage/'inputs'/(family+'.json'),'--backend',a.runtime/'run-transaction-session',
            '--output',stage/name,'--callback-batches'],True,cwd=empty)
        run(name+'-verify',[sys.executable,'-B','-m','fixtures.modern_selective.verify','--run',stage/name,
            '--output',stage/(name+'-verification.json')])
    check_identity()
    save(stage/'execution.json',dict(passed=True,networks=networks,closed=True,runtime_unchanged=True,
        fresh_python_root=str(fresh),monitored_driver_wall_seconds=elapsed,
        files={str(f.relative_to(stage)):sha(f) for f in stage.rglob('*') if f.is_file() and f.name!='execution.json'}))
    print('four complete modern selective conditions verified; stage closed',flush=True)


if __name__=='__main__':main()
