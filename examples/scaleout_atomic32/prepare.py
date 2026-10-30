"""Prepare sparse artificial32/64-bit transactions on a128-endpoint Clos."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def inputs():
    load=lambda name:json.loads((ROOT/name).read_bytes())
    topology=load('examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type']='NONE'
    topology['intra_host']['enabled']=False
    protocol=load('examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=64,chunk_bytes=256,switch_counter_storage='port_dense',
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=200000,wire_headers=True),queue_metrics=True)
    protocol['limits'].update(max_messages=128,max_queue_events=200000,queue_capture='events',
        max_sim_ns=5000000,wall_seconds=60,rss_bytes=2**30,address_bytes=2*2**30,
        artifact_bytes=64*2**20,result_json_bytes=32*2**20)
    connections=[dict(id=i,src=s,dst=d,plane=0,request_port=25000+2*i,response_port=25001+2*i,
                     source_nic=1,destination_nic=1,queue_depth=4)
                 for i,(s,d) in enumerate([(0,16),(1,16),(16,17)])]
    initial=bytearray(256)
    for offset,value,width in [(0,0x11223344,4),(4,0xfffffffe,4),(8,0x55667788,4),
                               (20,0xdeadbeef,4),(24,0x100000000,8)]:
        initial[offset:offset+width]=value.to_bytes(width,'little')
    windows=[dict(owner=16,key=7,address=8192,bytes=256,allow_atomic=True,
                  initialization='zero_fill/1',segments=[dict(offset=0,hex=initial.hex())]),
             dict(owner=17,key=8,address=12288,bytes=128,allow_atomic=True,
                  initialization='zero_fill/1',segments=[]),
             dict(owner=0,key=9,address=4096,bytes=64,allow_atomic=False,
                  initialization='zero_fill/1',segments=[dict(offset=0,hex=bytes(range(16)).hex())])]
    dep=lambda i,event='applied':dict(operation=i,event=event)
    def remote(i,c,verb,address,value,after,key=7):
        return dict(id=i,kind='remote',connection=c,verb=verb,key=key,address=address,value=value,after=after)
    def wait(key,address,value,bits=32):
        row=dict(key=key,address=address,compare='eq',value=value,reset_to=0)
        if bits==32:row['bits']=32
        return row
    operations=[dict(id=0,kind='remote',connection=0,verb='put',key=7,address=8256,
                     source=dict(key=9,address=4096,bytes=16),after=[]),
        remote(1,0,'fetch_add32',8196,3,[dep(0)]),
        remote(2,1,'fetch_add32',8196,0xffffffff,[dep(1)]),
        remote(3,0,'fetch_add32',8204,0xffffffff,[dep(1)]),
        remote(4,1,'fetch_add32',8208,1,[dep(2)]),
        remote(5,0,'fetch_add',8216,0xffffffffffffffff,[dep(3)]),
        dict(id=6,kind='local',rank=16,duration_ns=7,after=[dep(4),dep(5)],
             local_wait=[wait(7,8196,0),wait(7,8204,0xffffffff),wait(7,8208,1),wait(7,8216,0xffffffff,64)],
             copy=dict(source=dict(key=7,address=8256,bytes=16),target=dict(key=7,address=8320))),
        dict(id=7,kind='remote',connection=2,verb='put',key=8,address=12288,
             source=dict(key=7,address=8320,bytes=16),after=[dep(6,'completed')]),
        remote(8,2,'fetch_add32',12308,0xffffffff,[dep(7)],key=8),
        dict(id=9,kind='local',rank=17,duration_ns=0,after=[dep(8)],
             local_wait=[wait(8,12308,0xffffffff)],
             copy=dict(source=dict(key=8,address=12288,bytes=16),target=dict(key=8,address=12352))),
        remote(10,0,'fetch_add32',8208,5,[dep(6,'completed')])]
    work=dict(schema_version='moe-rdma-transactions/5',connections=connections,windows=windows,operations=operations,
        local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
        source_model=dict(capture='registered_window_at_publish/1',release='transaction_completed/1'),
        local_memory_model=dict(mode='guarded_copy/1',acquire='admission/1',capture='start/1',commit='completion/1'))
    reference=load('examples/scaleout_rdma_transactions/selective-chain.json')
    for key in ('dcqcn_reference','transport_recovery'):work[key]=reference[key]
    work['transport_recovery'].update(initial_rto_ns=17000,inflight_bytes=4096)
    loss=deepcopy(protocol)
    loss['faults']=[dict(node=16,port=1,message_id=0,kind='data',sequence=48,start_ns=0,end_ns=5000000,count=1),
                    dict(node=0,port=1,message_id=1,kind='data',sequence=0,start_ns=0,end_ns=5000000,count=1)]
    return topology,dict(normal=protocol,recovery=loss),work


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    topology,protocols,work=inputs()
    for name,value in [('topology-config.json',topology),('work.json',work),*[(n+'.json',v) for n,v in protocols.items()]]:
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology',
        '--config',str(out/'topology-config.json'),'--output',str(out/'topology')],cwd=ROOT,check=True)
    from moe_ub_cosim.scaleout_transactions import validate
    saved=json.loads((out/'topology/topology.json').read_bytes())
    for value in protocols.values():validate(saved,value,work)
    print(json.dumps(dict(prepared=True,gpu_endpoints=128,active_ranks=[0,1,16,17],
        operations=len(work['operations']),real_route_input=False,network_executed=False)))


if __name__=='__main__':main()
