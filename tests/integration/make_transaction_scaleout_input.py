"""Portable artificial transaction-capacity fixture, not a MoE workload."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def make():
    base = json.loads((ROOT/'examples/scaleout_rdma_transactions/selective-chain.json').read_text())
    work = {k:v for k,v in base.items() if k not in ('operations', 'windows', 'connections')}
    work['execution_limits'] = dict(profile='scaleout_ep32/1', max_connections=65, max_remote_operations=66, max_local_operations=3)
    work['self_peer_model'] = dict(mode='declared_service_fifo/1')
    work['registration_model'] = dict(mode='nic_scoped_aliases/1')
    work['connections'] = []; work['windows'] = []; work['registrations'] = []; work['operations'] = []
    for owner in range(32,64):
        for key in (1,2,3):
            work['windows'].append(dict(owner=owner,key=key,address=key*4096,allow_atomic=key==2,bytes=512,
                initialization='zero_fill/1',segments=[dict(offset=0,hex=owner.to_bytes(8,'little').hex())] if key==1 else []))
        for key in (1,2):
            work['registrations'].append(dict(id=len(work['registrations']),owner=owner,nic=1,key=100+key,
                window_key=key,address=key*4096,bytes=512,allow_atomic=key==2))
    for i in range(65):
        src=32+i%32; dst=src if i==64 else 32+(i%32+1+i//32)%32
        conn=dict(id=i,src=src,dst=dst,plane=0,source_nic=1,destination_nic=1,queue_depth=4)
        if i==64:conn['self_service']=dict(apply_ns=10,complete_ns=20)
        else:conn.update(request_port=24000+2*i,response_port=24001+2*i)
        work['connections'].append(conn)
        work['operations'].append(dict(id=i,kind='remote',connection=i,verb='put',key=102,address=8192+8*(i//32),
            source=dict(key=1,address=4096,bytes=8),after=[]))
    work['operations'].append(dict(id=65,kind='remote',connection=0,verb='fetch_add32',key=102,address=8192+256,value=1,after=[]))
    work['operations'].append(dict(id=66,kind='local',rank=33,duration_ns=10,after=[dict(operation=65,event='completed')],
        local_wait=[dict(key=2,address=8192+256+4*i,bits=32,compare='eq',value=1 if i==0 else 0) for i in range(32)]))
    work['operations'].append(dict(id=67,kind='local',rank=32,duration_ns=10,after=[dict(operation=i,event='completed') for i in range(67)]))
    work['operations'].append(dict(id=68,kind='local',rank=33,duration_ns=10,after=[dict(operation=67,event='completed')],
        copy=dict(source=dict(key=2,address=8192,bytes=8),target=dict(key=3,address=12288))))
    return work


if __name__=='__main__':
    import sys
    path=Path(sys.argv[1]);path.write_text(json.dumps(make(),indent=2)+'\n')
