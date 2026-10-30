"""Prepare two independent finite MoE calls on a shared128-endpoint Clos.

FourEP32 groups hold two complete deployments per call. No network runs here.
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
from moe_ub_cosim.service_transport import message_preflight

ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as stream:json.dump(v,stream,indent=2);stream.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-manifest',type=Path);p.add_argument('--target-files',type=int)
    p.add_argument('--target-deadline-ns',type=int,default=70000)
    p.add_argument('--healthy-deadline-ns',type=int,default=4000000)
    p.add_argument('--control-deadline-ns',type=int,default=4000000)
    a=p.parse_args();out=a.output.resolve()
    for t in (a.target_deadline_ns,a.healthy_deadline_ns,a.control_deadline_ns):
        if not 1<=t<=4999999:raise ValueError('deadline must be1..4999999ns')
    count=a.target_files if a.target_files is not None else (4 if a.source_manifest else 1)
    if not 1<=count<=32:raise ValueError('target requests must be1..32')
    subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_whole_call/prepare.py'),
        '--output',str(out)],cwd=ROOT,check=True)
    c=load(out/'service-whole.json')
    if a.source_manifest:
        manifest=load(a.source_manifest.resolve())
        if manifest['model']['name']!='deepseek-ai/DeepSeek-R1' or manifest['model']['hidden_dim']!=7168:
            raise ValueError('real-source example supports only declared R1 shape; do not relabel routes')
        for f in manifest['files']:f['file']['path']=str((ROOT/f['file']['path']).resolve())
        c['model'].update(name=manifest['model']['name'],hidden_size=7168,ffn_hidden_size=18432,experts=256,top_k=8,layers=2)
        c['workload']['context_tokens']=65536;c['route_replay']['layer_ids']=[3,4]
    else:
        manifest=load(Path(c['route_replay']['manifest']['path']))
        used={q['file_id'] for q in c['route_replay']['requests']}
        manifest['files']=[f for f in manifest['files'] if f['file_id'] in used]
    if not count<len(manifest['files'])<=count+32:raise ValueError('need target files plus1..32 distinct healthy files')
    save(out/'multicall-manifest.json',manifest)
    c['parallel']['groups']=[list(range(i,128,4)) for i in range(4)]
    groups=c['parallel']['groups'];c['route_replay'].update(manifest=dict(path=str(out/'multicall-manifest.json'),
        sha256=sha(out/'multicall-manifest.json')),budget=dict(max_files=64,max_bytes=64*2**20),requests=[
            dict(rank_id=groups[0 if i<count else 2][i if i<count else i-count],slot=0,
                file_id=f['file_id'],first_decode_entry=1) for i,f in enumerate(manifest['files'])])
    c['whole_call_replication']=dict(schema_version='moe-whole-call-replication/3',policy='delayed_complete_segment/1',
        initial_state='declared_equivalent_ready_kv/1',max_events=14,max_wakeup_events=16,calls=[
            dict(id='target',primary_group=0,replica_group=1,hedge_after_ns=a.target_deadline_ns),
            dict(id='healthy',primary_group=2,replica_group=3,hedge_after_ns=a.healthy_deadline_ns)])
    c['execution_events']['max_events']=65536
    replay=Replay(c,ROOT);waves=[make_wave(c,g,i,0,1,replay) for i,g in enumerate(groups)]
    keys=sorted({(m['src'],m['dst'],m['plane']) for w in waves for m in w['messages']})
    if len(keys)>1024:raise ValueError('example stream planning budget1024 exceeded')
    c['transport']['streams']=[dict(id=i,src=s,dst=d,plane=plane,wire_port=21000+i,
        source_nic=1,destination_nic=1,queue_depth=32) for i,(s,d,plane) in enumerate(keys)]
    c['transport']['limits'].update(max_streams=len(keys),max_work=2048,max_link_transmissions=200000)
    normal=load(out/'rdma.json');normal['limits'].update(max_messages=2048,queue_capture='events')
    incoming=Counter()
    for m in waves[0]['messages']:
        if m['phase']=='dispatch' and m['layer']==0:incoming[m['dst']]+=m['bytes']
    if not incoming:raise ValueError('target first dispatch has no remote data')
    target=min(incoming,key=lambda rank:(-incoming[rank],rank));local=deepcopy(normal)
    local['data_loss']=dict(kind='periodic_destination_data/1',numerator=1,denominator=1,offset=0,
        start_ns=0,end_ns=20000,nodes=[target])
    save(out/'rdma-multicall-normal.json',normal);save(out/'rdma-multicall-local.json',local)
    control=deepcopy(c)
    for call in control['whole_call_replication']['calls']:call['hedge_after_ns']=a.control_deadline_ns
    topology=load(out/'topology/topology.json')
    for service in (c,control):
        for protocol in (normal,local):
            validate(service,topology,protocol,replay)
            checker=message_preflight(topology,protocol,service)
            for wave in waves:checker.accept_wave(wave,0)
    save(out/'service-multicall.json',c);save(out/'service-control.json',control)
    save(out/'multicall-preparation.json',dict(passed=True,network_executed=False,simulated_endpoints=128,ep_sizes=[32]*4,
        target_logical_requests=count,healthy_logical_requests=len(manifest['files'])-count,
        unique_source_files=len(replay.selected['files']),source_kind=manifest['source']['kind'],
        source_dataset_revision=manifest['source']['dataset_revision'],source_bytes=replay.input_bytes,
        streams=len(keys),all_attempt_messages=sum(len(w['messages']) for w in waves),fault_target=target,
        deadlines=c['whole_call_replication']['calls'],control_deadline_ns=a.control_deadline_ns,
        scope='Finite declared two-layer single-Decode calls, preexisting replicas/KV; no numerical or hardware validation'))
    print(json.dumps(dict(passed=True,network_executed=False,independent_requests=len(manifest['files']),
        target_requests=count,healthy_requests=len(manifest['files'])-count,streams=len(keys),fault_target=target)))

if __name__=='__main__':main()
