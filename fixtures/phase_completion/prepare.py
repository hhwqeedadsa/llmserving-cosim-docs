"""Freeze necessary phase edges on existing source-checked Clos inputs."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.phase_completion import structure
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave,validate
from moe_ub_cosim.service_transport import message_preflight

ROOT=Path(__file__).resolve().parents[2]
def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')


def prepare(inputs,topologies,output):
    output.mkdir(parents=True,exist_ok=False)
    sources=load(ROOT/'docs/review_artifacts/invocation_semantics_20261023/sources.json')['sources']
    checked=[]
    for source in sources:
        path=ROOT/source['cache_path'];raw=path.read_bytes()
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if sha(path)!=source['sha256'] or len(raw)!=source['bytes'] or blob!=source['git_blob_sha']:
            raise ValueError('fixed source cache differs: '+str(path))
        checked.append({k:source[k] for k in ('path','commit','sha256','public_url')})
    summary=[]
    for n in (128,256,512):
        c=load(inputs/f'service{n}.json');p=load(inputs/f'rdma{n}.json')
        t=load(topologies/f'clos{n}/topology/topology.json')
        old=deepcopy(c);c['phase_completion']=dict(mode='ep_transport_flush/1')
        replay=Replay(c,ROOT);validate(c,t,p,replay);preflight=message_preflight(t,p,c)
        folder=output/str(n);folder.mkdir();topo=folder/'topology';topo.mkdir();save(topo/'topology.json',t)
        gate_count=0;members=0;message_count=0
        waves=folder/'planned';waves.mkdir()
        for index,group in enumerate(c['parallel']['groups']):
            for step in range(c['workload']['decode_steps']):
                wave=make_wave(c,group,index,step,1,replay)
                previous=make_wave(old,group,index,step,1,replay)
                if wave['messages']!=previous['messages'] or wave['routes']!=previous['routes']:
                    raise ValueError('phase gate changed logical bytes or sources')
                gates=structure(wave,c);gate_count+=len(gates);members+=sum(len(g['members']) for g in gates)
                message_count+=len(wave['messages']);preflight.accept_wave(wave,0)
                save(waves/(wave['graph_id']+'.json'),wave)
        save(folder/'service.json',c);save(folder/'rdma.json',p)
        summary.append(dict(gpus=n,independent_requests=len(replay.records),ep_sizes=list(map(len,c['parallel']['groups'])),
            gates=gate_count,gate_members=members,messages=message_count,network_executed=False,
            logical_message_descriptors_unchanged=True,phase_edges_verified=True))
    save(output/'preparation.json',dict(passed=True,fixed_sources=checked,scales=summary,
        semantics='necessary transport completion ordering with zero coordination cost; not full DeepEP',
        input_hashes={str(p.relative_to(output)):sha(p) for p in output.rglob('*') if p.is_file()}))
    print(json.dumps(dict(passed=True,sources_checked=len(checked),scales=summary)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','topologies','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();prepare(a.inputs.resolve(),a.topologies.resolve(),a.output.resolve())
