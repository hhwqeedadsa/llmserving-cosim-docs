"""Generate synthetic causal All-to-All/local/All-to-All groups, not MoE inference.

Public example generator; standard library only. No platform/test imports.
Group g contains g+k*(N/R), separating strided rank labels. Physical placement
is decided by the topology, not by this program. Self traffic is excluded.
"""
import argparse
import json
from pathlib import Path


def program(gpus,ep,rounds,payload,local_ns):
    if not (type(gpus) is int and 2<=gpus<=1024 and type(ep) is int and 2<=ep<=min(gpus,32) and gpus%ep==0):
        raise ValueError('require 2..1024 GPUs, EP=2..32 and an integer number of groups')
    if not (type(rounds) is int and 1<=rounds<=64 and type(payload) is int and 1<=payload<=1048576 and type(local_ns) is int and 0<=local_ns<=5000000):
        raise ValueError('round/payload/local-duration bounds exceeded')
    nstreams=gpus*(ep-1);nwork=rounds*(2*nstreams+2*gpus)
    if nstreams>4096 or nwork>65536 or 2*rounds*payload>64*1024*1024:
        raise ValueError('program exceeds stream/work/sequence limits')
    groups=[[g+k*(gpus//ep) for k in range(ep)] for g in range(gpus//ep)]
    pairs=[(src,dst) for group in groups for src in group for dst in group if src!=dst]
    streams=[dict(id=i,src=src,dst=dst,plane=0,wire_port=10000+i,source_nic=1,destination_nic=1,queue_depth=2) for i,(src,dst) in enumerate(pairs)]
    work=[];previous={};group_of={rank:group for group in groups for rank in group}
    def add(**node):
        node['id']=len(work);work.append(node);return node['id']
    def dep(identity,event):return dict(work=identity,event=event)
    for _ in range(rounds):
        dispatch={}
        for sid,(src,dst) in enumerate(pairs):
            dispatch[src,dst]=add(kind='write',stream=sid,bytes=payload,after=[] if src not in previous else [dep(previous[src],'completed')])
        compute={}
        for rank in range(gpus):
            peers=[p for p in group_of[rank] if p!=rank]
            compute[rank]=add(kind='local',rank=rank,duration_ns=local_ns,
                after=[dep(dispatch[p,rank],'accepted') for p in peers]+[dep(dispatch[rank,p],'acked') for p in peers])
        combine={}
        for sid,(src,dst) in enumerate(pairs):
            combine[src,dst]=add(kind='write',stream=sid,bytes=payload,after=[dep(compute[src],'completed')])
        previous={}
        for rank in range(gpus):
            peers=[p for p in group_of[rank] if p!=rank]
            previous[rank]=add(kind='local',rank=rank,duration_ns=0,
                after=[dep(combine[p,rank],'accepted') for p in peers]+[dep(combine[rank,p],'acked') for p in peers])
    return dict(schema_version='moe-rdma-stream-work/4',
        limits=dict(max_streams=nstreams,max_work=nwork,max_link_transmissions=2000000),
        local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),streams=streams,work=work)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gpus',type=int,required=True);p.add_argument('--ep',type=int,required=True)
    p.add_argument('--rounds',type=int,default=2);p.add_argument('--payload-bytes',type=int,default=768)
    p.add_argument('--local-ns',type=int,default=100);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();w=program(a.gpus,a.ep,a.rounds,a.payload_bytes,a.local_ns)
    with a.output.open('x') as f:json.dump(w,f,indent=2);f.write('\n')


if __name__=='__main__':main()
