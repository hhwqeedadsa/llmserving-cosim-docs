"""Pure preflight acceptance witness and public negative-startup workflow.

The marker backend is deliberately NOT a network simulator. Success means it
was never started for a configuration the fixed native Open cannot accept.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.modern_native_layout import ModernNativeLayout
from moe_ub_cosim.scaleout_streams import validate
from moe_ub_cosim.scaleout_topology import generate


def load(p): return json.loads(p.read_bytes())
def save(p,v): p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def before(repo,output):
    assert not output.exists();output.mkdir()
    config=load(repo/'examples/modern_deepep_native/topology.json')
    config['fabric'].update(rails=2,planes=1,upper_tier_scope='rail')
    config['nic']['rails_by_gpu_slot']=[[0,1],[0,1]]
    topology,_=generate(config)
    modern=load(repo/'examples/modern_deepep_native/input.json')
    for rank in modern['deployment']:
        for dev in rank['devices']:dev['plane']=0
    protocol=load(repo/'examples/modern_deepep_native/rdma.json')
    work=load(repo/'examples/scaleout_rdma_streams/work.json')
    # Old validators accept this even though the fixed C++ Open requires [1].
    validate(topology,protocol,work)
    layout=ModernNativeLayout(topology,protocol,modern)
    for name,value in (('topology-config.json',config),('topology.json',topology),('modern.json',modern),
                       ('protocol.json',protocol),('work.json',work),('initial.json',layout.initial)):
        save(output/name,value)
    sources={}
    for name in ('python/moe_ub_cosim/scaleout_streams.py','backend/rdma/persistent_streams.cc','backend/rdma/fabric.cc'):
        p=repo/name;raw=p.read_text();(output/(p.name+'.before')).write_text(raw)
        sources[name]=sha(p)
    nodes={n['id']:n for n in topology['nodes']};adj={n:[] for n in nodes}
    for link in topology['links']:
        adj[link['src']].append((link['dst'],link['src_port']))
        adj[link['dst']].append((link['src'],link['dst_port']))
    paths=[]
    def walk(path,nic):
        current=path[-1]
        if current==4:paths.append(dict(nodes=path,source_nic=nic,hops=len(path)-1));return
        for other,port in adj[current]:
            if other in path or (nodes[other]['kind']=='gpu' and other!=4):continue
            walk(path+[other],port if current==0 else nic)
    walk([0],None)
    best=min(r['hops'] for r in paths);shortest=[r for r in paths if r['hops']==best]
    assert len(shortest)==2 and {r['source_nic'] for r in shortest}=={1,2} and best==6
    save(output/'witness.json',dict(passed=True,old_stream_validator_accepted=True,old_modern_layout_accepted=True,
        same_plane=0,rails=2,shortest_paths=shortest,native_source_guard_requires=[1],
        native_failure_inferred_from_fixed_source_not_executed=True,source_hashes=sources,
        inputs={p.name:sha(p) for p in output.glob('*.json')},network_executions=0))


def after(repo,previous,output):
    assert not output.exists();output.mkdir();empty=output/'empty-cwd';empty.mkdir()
    marker=output/'backend-was-started'
    backend=output/'marker-backend'
    backend.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("started")\nraise SystemExit(97)\n')
    backend.chmod(0o755)
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout'];records=[]
    def run(label,argv,expected):
        done=subprocess.run(list(map(str,argv)),cwd=empty,env=env,capture_output=True,text=True,timeout=60)
        (output/(label+'.stdout')).write_text(done.stdout);(output/(label+'.stderr')).write_text(done.stderr)
        records.append(dict(label=label,command=list(map(str,argv)),cwd=str(empty),exit_code=done.returncode))
        save(output/'commands.json',records)
        assert (done.returncode==0)==expected,(label,done.stderr)
        return done
    run('topology',[*cli,'topology','--config',previous/'topology-config.json','--output',output/'topology'],True)
    for command,config in (('rdma-stream-probe','work.json'),('rdma-transaction-probe','initial.json'),
                           ('modern-deepep-native','modern.json')):
        destination=output/command
        done=run(command,[*cli,command,'--topology',output/'topology','--protocol',previous/'protocol.json',
                 '--config',previous/config,'--backend',backend,'--output',destination],False)
        assert 'unique shortest NIC' in done.stderr and 'candidates [1, 2]' in done.stderr
        assert not destination.exists() and not marker.exists()
    # A supported dual-plane input still passes the real pure admission chain.
    topology,_=generate(load(repo/'examples/modern_deepep_native/topology.json'))
    ModernNativeLayout(topology,load(previous/'protocol.json'),load(repo/'examples/modern_deepep_native/input.json'))
    save(output/'verification.json',dict(passed=True,public_commands=4,rejected_before_output_and_backend=3,
        marker_backend_started=marker.exists(),supported_dual_plane_admitted=True,network_executions=0,
        fake_backend_is_negative_startup_check_only=True,commands=records))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('before','after'))
    for name in ('repository','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--previous',type=Path)
    a=p.parse_args()
    if a.action=='before':before(a.repository.resolve(),a.output.resolve())
    else:after(a.repository.resolve(),a.previous.resolve(),a.output.resolve())


if __name__=='__main__':main()
