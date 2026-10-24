"""Freeze all four functional inputs and current runtime identities before execution."""
import argparse
from copy import deepcopy
from pathlib import Path
from stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','runtime'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();s=a.stage.resolve();old=a.runtime.resolve()
    build=load(old/'build.json')
    assert all(sha(old/'bin'/n)==h for n,h in build['binaries'].items())
    assert all(sha(p)==h for p,h in build['libraries'].items())
    assert all(sha(repo/p)==h for p,h in build['sources'].items())
    inputs=s/'inputs';inputs.mkdir();save(inputs/'topology.json',load(repo/'examples/scaleout_nack_grace/topology.json'))
    base=load(repo/'examples/scaleout_nack_grace/work.json');protocol=load(repo/'examples/scaleout_nack_grace/protocol.json')
    healthy=deepcopy(protocol);del healthy['link_failures'];del healthy['local_failover']
    detour=deepcopy(base);detour['streams']=[dict(id=12,src=6,dst=0,plane=0,wire_port=21012,source_nic=1,destination_nic=1,queue_depth=8)]
    detour['work']=[dict(id=0,kind='write',stream=12,bytes=65536,after=[]),
        dict(id=1,kind='local',rank=0,duration_ns=100,after=[dict(work=0,event='accepted')]),
        dict(id=2,kind='local',rank=6,duration_ns=100,after=[dict(work=0,event='acked')])]
    detour['transport_recovery'].update(receive_buffer_bytes=131072,inflight_bytes=129073)
    c=deepcopy(protocol);c.update(payload_bytes=2000,chunk_bytes=8000);c['link_failures']['events'][0]['route_update_delay_ns']=0
    save(inputs/'transient-detour-work.json',detour);save(inputs/'transient-detour-protocol.json',c)
    def fault(count=1):return dict(node=4,port=0,message_id=2,kind='data',sequence=0,start_ns=0,end_ns=5000000,count=count)
    rto=deepcopy(base);rto['transport_recovery']['reordering']['wait_ns']=80000
    rto['work']=[dict(id=0,kind='write',stream=2,bytes=4000,after=[]),
        dict(id=1,kind='local',rank=4,duration_ns=100,after=[dict(work=0,event='accepted')]),
        dict(id=2,kind='write',stream=2,bytes=2000,after=[dict(work=0,event='acked'),dict(work=1,event='completed')])]
    c=deepcopy(healthy);c['faults']=[fault()]
    save(inputs/'rto-preempts-work.json',rto);save(inputs/'rto-preempts-protocol.json',c)
    failed=deepcopy(base);failed['transport_recovery'].update(max_retries=1,on_exhausted='isolate_stream')
    failed['transport_recovery']['reordering']['wait_ns']=80000
    failed['work']=[dict(id=0,kind='write',stream=2,bytes=4000,after=[]),dict(id=1,kind='write',stream=8,bytes=4000,after=[])]
    c=deepcopy(healthy);c['faults']=[fault(16)]
    save(inputs/'source-isolated-work.json',failed);save(inputs/'source-isolated-protocol.json',c)
    tx=load(repo/'examples/scaleout_rdma_transactions/selective-chain.json')
    tx['transport_recovery']['reordering']=dict(mode='nack_grace/1',wait_ns=16000,max_events=10000)
    c=deepcopy(healthy);f=fault();f['message_id']=0;c['faults']=[f]
    save(inputs/'transaction-work.json',tx);save(inputs/'transaction-protocol.json',c)
    save(s/'runtime.json',dict(binaries={str(old/'bin'/n):h for n,h in build['binaries'].items()},
        libraries=build['libraries'],sources=build['sources'],platform_base='474d4e7 plus current bounded NACK grace working source',
        no_native_build=True))
    save(s/'inputs.json',dict(files={str(p.relative_to(s)):sha(p) for p in inputs.iterdir()},
        conditions=['transient-detour','rto-preempts','source-isolated','transaction'],network_cap=4,
        generated_before_first_network=True,scope='functional edge cases, not research performance'))

if __name__=='__main__':main()
