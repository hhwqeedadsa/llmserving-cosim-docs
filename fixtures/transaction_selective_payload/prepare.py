"""Functional transaction programs, not MoE workloads or performance samples."""
import argparse
from copy import deepcopy
import json
from pathlib import Path


def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def base(repo):
    w=json.loads((repo/'examples/scaleout_rdma_transactions/sparse-windows.json').read_text())
    reference=json.loads((repo/'examples/scaleout_rdma_streams/work-selective.json').read_text())
    w['dcqcn_reference']=reference['dcqcn_reference']
    w['transport_recovery']=reference['transport_recovery']
    w['connections'][0]['queue_depth']=8;w['connections'][1]['queue_depth']=8
    return w


def atomic(repo):
    w=base(repo);w['connections']=w['connections'][:1]
    w['windows']=[dict(owner=4,key=8,address=8192,bytes=65536,allow_atomic=True,
        initialization='zero_fill/1',segments=[dict(offset=0,hex=(7).to_bytes(8,'little').hex())])]
    w['operations']=[dict(id=i,kind='remote',connection=0,verb='fetch_add',key=8,address=8192,value=i+1,after=[]) for i in range(8)]
    return w


def chain(repo,initial=7):
    w=base(repo)
    data=bytes((i*17+3)%256 for i in range(2600)).hex()
    w['windows']=[dict(owner=o,key=k,address=a,bytes=65536,allow_atomic=True,initialization='zero_fill/1',segments=segs)
        for o,k,a,segs in [(0,7,4096,[dict(offset=0,hex=data)]),
            (4,8,8192,[dict(offset=4096,hex=initial.to_bytes(8,'little').hex())]),(1,9,12288,[])]]
    dep=lambda i,event='completed':[dict(operation=i,event=event)]
    w['operations']=[
        dict(id=0,kind='remote',connection=0,verb='put',key=8,address=8192,source=dict(key=7,address=4096,bytes=2600),after=[]),
        dict(id=1,kind='remote',connection=0,verb='fetch_add',key=8,address=12288,value=3,after=dep(0,'applied')),
        dict(id=2,kind='local',rank=4,duration_ns=10,copy=dict(source=dict(key=8,address=8192,bytes=2600),target=dict(key=8,address=16384)),
            local_wait=[dict(key=8,address=12288,compare='eq',value=initial+3,reset_to=0)],after=dep(1,'applied')),
        dict(id=3,kind='remote',connection=1,verb='put',key=9,address=12288,source=dict(key=8,address=16384,bytes=2600),after=dep(2)),
        dict(id=4,kind='local',rank=0,duration_ns=1000,copy=dict(source=dict(key=7,address=4096,bytes=2600),target=dict(key=7,address=24576)),after=dep(0)),
        dict(id=5,kind='remote',connection=1,verb='fetch_add',key=9,address=16384,value=1,after=dep(3,'applied')),
        dict(id=6,kind='local',rank=1,duration_ns=0,copy=dict(source=dict(key=9,address=12288,bytes=2600),target=dict(key=9,address=24576)),
            local_wait=[dict(key=9,address=16384,compare='eq',value=1,reset_to=0)],after=dep(5,'applied')),
        dict(id=7,kind='remote',connection=0,verb='put',key=8,address=32768,hex=(initial+100).to_bytes(8,'little').hex(),after=dep(1))]
    return w


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();a.output.mkdir(exist_ok=False)
    save(a.output/'atomic.json',atomic(a.repository));save(a.output/'chain.json',chain(a.repository))
    save(a.output/'online.json',chain(a.repository,17))
    explicit=json.loads((a.repository/'examples/scaleout_rdma_transactions/submission-prefix.json').read_text())
    b=base(a.repository);explicit.update(dcqcn_reference=b['dcqcn_reference'],transport_recovery=b['transport_recovery'])
    save(a.output/'explicit.json',explicit)
    topo=json.loads((a.repository/'examples/scaleout_rdma/small-clos-100g.json').read_text());topo['deployment']['gpu_type']='NONE'
    save(a.output/'topology.json',topo)
    protocol=json.loads((a.repository/'examples/scaleout_link_failure/stream-rdma.json').read_text())
    del protocol['link_failures'];del protocol['ecn_by_rate']['25000000000'];protocol.pop('mmu_snapshot',None)
    save(a.output/'protocol.json',protocol)
    for name,node,stream,sequence in [('request',4,0,0),('reply-tail',0,1,280),('middle',4,0,1000)]:
        c=deepcopy(protocol);c['faults']=[dict(node=node,port=0,message_id=stream,kind='data',sequence=sequence,start_ns=0,end_ns=5000000,count=1)]
        save(a.output/(name+'-protocol.json'),c)


if __name__=='__main__':main()
