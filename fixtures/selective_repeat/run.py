"""Bounded functional conditions for selective repeat, not a performance study."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','backend','old_backend'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    inputs=stage/'inputs';inputs.mkdir();runs=stage/'network';runs.mkdir()
    base=load(repo/'examples/scaleout_rdma_streams/work-selective.json')
    topology=load(repo/'examples/scaleout_rdma/small-clos-100g.json');topology['deployment']['gpu_type']='NONE'
    save(inputs/'topology.json',topology)
    protocol=load(repo/'examples/scaleout_link_failure/stream-rdma.json');del protocol['link_failures']
    del protocol['ecn_by_rate']['25000000000']
    protocol['packet_capture']['max_events']=100000;protocol['limits']['queue_capture']='summary'
    save(inputs/'protocol.json',protocol)
    commands=[]
    def cli(label,tail):
        cmd=[sys.executable,'-B','-m','moe_ub_cosim','scaleout',*map(str,tail)]
        with (stage/(label+'.stdout.log')).open('w') as out,(stage/(label+'.stderr.log')).open('w') as err:
            result=subprocess.run(cmd,cwd=repo,stdout=out,stderr=err,timeout=60)
        commands.append(dict(command=cmd,cwd=str(repo),exit_code=result.returncode));save(stage/'commands.json',commands)
        assert result.returncode==0,label
    cli('topology',['topology','--config',inputs/'topology.json','--output',stage/'topology'])
    names=['healthy','head','middle','tail','sack-loss','ack-loss','link-failure','karn-tail']
    for name in names:
        w=deepcopy(base);c=deepcopy(protocol)
        if name not in ('healthy','link-failure'):w['work'][0]['bytes']=4000
        fault=[]
        if name in ('head','middle','tail','sack-loss','karn-tail'):
            sequence=0 if name in ('head','sack-loss') else 1000 if name=='middle' else 3000
            fault.append(dict(node=4,port=0,message_id=0,kind='data',sequence=sequence,start_ns=0,end_ns=5000000,count=1))
        if name=='sack-loss':fault.append(dict(node=0,port=0,message_id=0,kind='nack',sequence=0,start_ns=0,end_ns=5000000,count=1))
        if name=='ack-loss':fault.append(dict(node=0,port=0,message_id=0,kind='ack',sequence=4000,start_ns=0,end_ns=5000000,count=1))
        if fault:c['faults']=fault
        if name=='karn-tail':w['transport_recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=20000,granularity_ns=1)
        if name=='link-failure':c['link_failures']=dict(mode='switch_links/1',events=[dict(src=8,dst=10,time_ns=4000,route_update_delay_ns=12000)])
        save(inputs/(name+'-work.json'),w);save(inputs/(name+'-protocol.json'),c)
        cli(name,['rdma-stream-probe','--topology',stage/'topology','--protocol',inputs/(name+'-protocol.json'),
                  '--config',inputs/(name+'-work.json'),'--backend',a.backend,'--output',runs/name])
        r=load(runs/name/'result.json')
        assert r['packet_capture']['complete'] and r['passed'] and r['drained']
        assert not fault or r['fault_injection']['drop_counts']==[1]*len(fault)
    default=repo/'examples/scaleout_rdma_streams/work-reference.json'
    for label,backend in [('default-old',a.old_backend),('default-new',a.backend)]:
        cli(label,['rdma-stream-probe','--topology',stage/'topology','--protocol',inputs/'protocol.json',
                   '--config',default,'--backend',backend,'--output',runs/label])
    assert load(runs/'default-old/result.json')==load(runs/'default-new/result.json'),'default complete result changed'
    save(stage/'run.json',dict(passed=True,selective_cases=names,network_executions=10,default_complete_result_equal=True))
    print(json.dumps(dict(passed=True,conditions=10)))


if __name__=='__main__':main()
