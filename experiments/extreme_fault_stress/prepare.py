"""Generate explicit synthetic routing and causal byte workloads."""
import argparse
from copy import deepcopy
from pathlib import Path
from experiments.extreme_fault_stress.stage import load, save, sha


def make_work(c, counts):
    sources, owners = c['sources'], c['owners']; streams = []
    for src, dst in [(s,o) for s in sources for o in owners]+[(o,s) for o in owners for s in sources]:
        streams.append(dict(id=len(streams), src=src, dst=dst, plane=0,
                            wire_port=23000+8*src+dst, source_nic=1, destination_nic=1, queue_depth=1024))
    bindings = {(x['src'], x['dst']): x['id'] for x in streams}
    routes = []
    for src in sources:
        row = []
        for owner, count in zip(owners, counts):
            row += [dict(owner=owner, expert=(owner-owners[0])*c['experts_per_owner']+i%c['experts_per_owner']) for i in range(count)]
        assert len(row) == c['tokens_per_source_per_step']; routes.append(dict(src=src, tokens=row))
    work, labels, previous = [], [], []
    def add(kind, after, label, **fields):
        wid=len(work); work.append(dict(id=wid, kind=kind, after=after, **fields)); labels.append(dict(id=wid, **label)); return wid
    ref=lambda wid, event: dict(work=wid,event=event)
    for step in range(c['steps']):
        start={s:add('local', previous, dict(step=step,phase='input',rank=s), rank=s,duration_ns=0) for s in sources}
        dispatch={}
        for src in sources:
            for owner, count in zip(owners, counts):
                dispatch[src,owner]=add('write',[ref(start[src],'completed')],dict(step=step,phase='dispatch',src=src,dst=owner),
                    stream=bindings[src,owner],bytes=count*c['hidden_dim']*c['activation_bytes_per_element'])
        experts={o:add('local',[ref(dispatch[s,o],'accepted') for s in sources],dict(step=step,phase='experts',rank=o),
                       rank=o,duration_ns=c['expert_stage_ns']) for o in owners}
        combine={}
        for owner, count in zip(owners, counts):
            for src in sources:
                combine[owner,src]=add('write',[ref(experts[owner],'completed')],dict(step=step,phase='combine',src=owner,dst=src),
                    stream=bindings[owner,src],bytes=count*c['hidden_dim']*c['activation_bytes_per_element'])
        merge={s:add('local',[ref(combine[o,s],'accepted') for o in owners],dict(step=step,phase='merge',rank=s),
                     rank=s,duration_ns=c['merge_ns']) for s in sources}
        done=add('local',[*[ref(x,'acked') for x in dispatch.values()],*[ref(x,'acked') for x in combine.values()],
                          *[ref(x,'completed') for x in merge.values()]],dict(step=step,phase='step_done',rank=0),rank=0,duration_ns=0)
        previous=[ref(done,'completed')]
    return dict(schema_version='moe-rdma-stream-work/4',streams=streams,work=work,
        limits=dict(max_streams=32,max_work=128,max_link_transmissions=200000),
        local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
        dcqcn_reference=dict(mode='figure7_reference/1',np_interval_ns=50000,alpha_interval_ns=55000,
            recovery_interval_ns=55000,recovery_bytes=10000000,min_rate_bps=100000000,additive_bps=40000000,
            hyper_bps=50000000,fast_recovery_rounds=5,gain=1/256,cnp_model_bytes=48,max_events=30000),
        transport_recovery=dict(mode='selective_repeat/1',initial_rto_ns=80000,max_rto_ns=640000,max_retries=3,
            max_events=30000,on_exhausted='isolate_stream',receive_buffer_bytes=2097152,inflight_bytes=2095153,
            reordering=dict(mode='nack_grace/1',wait_ns=16000,max_events=30000))), routes, labels


def main():
    p=argparse.ArgumentParser()
    for key in ('repository','config','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args(); repo=a.repository.resolve(); out=a.output.resolve(); c=load(a.config)
    assert not out.exists() and len(c['cases'])==6 and c['steps']==2 and c['top_k']==1
    assert c['sources']==list(range(4)) and c['owners']==list(range(4,8))
    for key in ('uniform_owner_tokens','hotspot_owner_tokens'):
        assert len(c[key])==4 and all(type(x)is int and x>0 for x in c[key])
        assert sum(c[key])==c['tokens_per_source_per_step']
    out.mkdir(); (out/'observed').mkdir(); (out/'truth').mkdir(); save(out/'config.json',c)
    topology=load(repo/'examples/scaleout_link_failure/queued-topology.json')
    # Export compatibility label only; this experiment does not execute GPUs.
    topology['deployment']['gpu_type']='A100'
    for name in ('tor_agg','agg_core','tor_core'):topology['links'][name]['rate_bps']=c['upper_rate_bps']
    save(out/'observed/topology.json',topology)
    base=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=2000,chunk_bytes=8000,ack_interval_bytes=1,
        base_rtt_ns=14000,switch_buffer_bytes=8388608,seed=c['seed'],queue_metrics=True,
        ecn_by_rate={str(rate):dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0) for rate in (100000000000,c['upper_rate_bps'])},
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000,wire_headers=True),
        limits=dict(max_gpus=8,max_switches=10,max_links=32,max_messages=128,max_queue_events=1,queue_capture='summary',
            max_sim_ns=5000000,wall_seconds=60,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=64*2**20,result_json_bytes=32*2**20))
    for name in ('uniform','hotspot'):
        w,r,l=make_work(c,c[name+'_owner_tokens']); assert len(w['work'])==90
        save(out/'observed'/(name+'-work.json'),w);save(out/'observed'/(name+'-labels.json'),l)
        save(out/'truth'/(name+'-routes.json'),r)
    for case in c['cases']:
        protocol=deepcopy(base)
        if case['failures']:
            protocol['link_failures']=dict(mode='switch_links/1',events=[dict(src=s,dst=d,time_ns=t,route_update_delay_ns=12000) for s,d,t in case['failures']])
            protocol['local_failover']=dict(mode='live_ecmp/1')
        save(out/'observed'/(case['name']+'-protocol.json'),protocol)
    files={str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}
    save(out/'manifest.json',dict(schema='moe-extreme-fault-inputs/1',files=files,cases=c['cases'],synthetic=True,hardware_calibrated=False))
    print('Prepared six synthetic conditions; no networks executed')


if __name__=='__main__':main()
