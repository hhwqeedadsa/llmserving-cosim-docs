"""Prepare eager complete-segment replicas; optional existing real source manifest.

No downloads, execution, numerical tensor checks or hardware calibration.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave,validate

ROOT=Path(__file__).resolve().parents[2]


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-manifest',type=Path)
    p.add_argument('--max-source-files',type=int,default=4)
    p.add_argument('--include-primary-control',action='store_true',help='Also prepare a no-redundancy control on the identical deployment')
    p.add_argument('--hedge-after-ns',type=int,help='Also prepare delayed full replicas with this native deadline')
    a=p.parse_args();out=a.output.resolve()
    if not 1<=a.max_source_files<=16:raise ValueError('max source files must be 1..16')
    if a.hedge_after_ns is not None and not 1<=a.hedge_after_ns<=4999999:
        raise ValueError('hedge deadline must be 1..4999999 ns')
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_source_ack_watchdog/prepare.py'),
                    '--output',str(out)],cwd=ROOT,check=True)
    c=load(out/'service-watchdog.json');c.pop('source_ack_watchdog');c.pop('contribution_tracking')
    c['workload']['decode_steps']=1
    if a.source_manifest:
        manifest=load(a.source_manifest.resolve());manifest['files']=manifest['files'][:a.max_source_files]
        for f in manifest['files']:f['file']['path']=str((ROOT/f['file']['path']).resolve())
        save(out/'whole-source-manifest.json',manifest)
        c['model'].update(name=manifest['model']['name'],hidden_size=manifest['model']['hidden_dim'],
            ffn_hidden_size=18432,experts=256,top_k=8,layers=2)
        c['workload']['context_tokens']=65536
        c['route_replay'].update(manifest=dict(path=str(out/'whole-source-manifest.json'),sha256=sha(out/'whole-source-manifest.json')),
            layer_ids=[3,4],requests=[dict(rank_id=2*i,slot=0,file_id=f['file_id'],first_decode_entry=1)
                for i,f in enumerate(manifest['files'])],budget=dict(max_files=a.max_source_files,max_bytes=16*2**20))
    else:
        c['route_replay']['requests']=[r for r in c['route_replay']['requests'] if r['rank_id'] in c['parallel']['groups'][0]]
    c['whole_call_replication']=dict(schema_version='moe-whole-call-replication/1',policy='eager_complete_segment/1',
        initial_state='declared_equivalent_ready_kv/1',max_events=7,calls=[dict(id='segment0',primary_group=0,replica_group=1)])
    replay=Replay(c,ROOT);waves=[make_wave(c,g,i,0,1,replay) for i,g in enumerate(c['parallel']['groups'])]
    keys=sorted({(m['src'],m['dst'],m['plane']) for w in waves for m in w['messages']})
    c['transport']['streams']=[dict(id=i,src=s,dst=d,plane=plane,wire_port=21000+i,
        source_nic=1,destination_nic=1,queue_depth=32) for i,(s,d,plane) in enumerate(keys)]
    c['transport']['limits']['max_streams']=len(keys)
    incoming=Counter()
    for m in waves[0]['messages']:
        if m['phase']=='dispatch' and m['layer']==0:incoming[m['dst']]+=m['bytes']
    target=min(incoming,key=lambda r:(-incoming[r],r))
    normal=load(out/'rdma.json');local=load(out/'rdma-transient.json')
    local['data_loss']['nodes']=[target]
    save(out/'rdma-whole-local.json',local)
    topology=load(out/'topology/topology.json')
    for protocol in (normal,local):validate(c,topology,protocol,Replay(c,ROOT))
    save(out/'service-whole.json',c)
    if a.hedge_after_ns is not None:
        delayed=deepcopy(c)
        delayed['whole_call_replication'].update(schema_version='moe-whole-call-replication/2',
            policy='delayed_complete_segment/1',hedge_after_ns=a.hedge_after_ns,max_wakeup_events=4)
        for protocol in (normal,local):validate(delayed,topology,protocol,Replay(delayed,ROOT))
        save(out/'service-delayed.json',delayed)
    if a.include_primary_control:
        primary=deepcopy(c);primary.pop('whole_call_replication')
        primary['route_replay']['request_layout']='sparse_active_groups/1'
        for protocol in (normal,local):validate(primary,topology,protocol,Replay(primary,ROOT))
        save(out/'service-primary.json',primary)
    save(out/'whole-preparation.json',dict(passed=True,network_executed=False,simulated_endpoints=128,
        expert_parallel_groups=[64,64],source_kind=replay.manifest['source']['kind'],
        unique_source_files=len(replay.selected['files']),logical_requests=len(replay.records)//2,
        execution_attempt_requests=len(replay.records),decode_steps=1,source_layers=replay.layers,
        preexisting_complete_replicas=2,equivalent_ready_kv='declared, not numerical verification',
        shared_streams=len(keys),fault_target=target,source_bytes=replay.input_bytes,
        conditions=['eager normal','same eager local DATA fault [0,20us)'],
        scope='complete finite MoE segment result consumer; not full R1, delayed retry or net recovery benefit'))
    print(json.dumps(dict(passed=True,network_executed=False,logical_requests=len(replay.records)//2,
        execution_attempt_requests=len(replay.records),fault_target=target)))


if __name__=='__main__':main()
