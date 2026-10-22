"""Prepare one preregistered 1024-GPU short workload; independently read public output."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
def read(path):return json.loads(path.read_text())
def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def prepare(output):
    output.mkdir(exist_ok=False)
    topology=dict(schema_version='moe-scaleout-topology/2',
        deployment=dict(pods=8,segments_per_pod=1,servers_per_segment=16,gpus_per_server=8,gpu_type='A100'),
        fabric=dict(rails=1,planes=1,tors_per_segment_per_rail=4,upper_tier_scope='shared',
            aggs_per_pod_per_group=2,cores_per_group=2,tor_uplinks=2,agg_uplinks=1,core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0] for _ in range(8)],total_rate_bps=400000000000),
        links={name:dict(rate_bps=400000000000,delay_ns=1000) for name in ('access','tor_agg','agg_core','tor_core')},
        port_limits=dict(gpu=16,tor=128,agg=128,core=128),budget=dict(max_nodes=8192,max_links=131072,max_switches=1024),
        intra_host=dict(kind='nonblocking_chunked_crossbar',enabled=True,rate_bps=3600000000000,delay_ns=100,chunk_bytes=16384))
    groups=[[2*j,2*j+1,512+2*j,513+2*j] for j in range(256)]
    service=dict(schema_version='moe-scaleout-service/1',
        model=dict(name='host-scale-synthetic-H1024-E16-K2-one-layer',hidden_size=1024,ffn_hidden_size=4096,
            experts=16,top_k=2,activation_bytes=2,layers=1,gpu_type='A100'),
        workload=dict(batch_per_rank=1,decode_steps=2,context_tokens=2048,route_seed=11,hotspot_fraction=0),
        parallel=dict(groups=groups),compute=dict(source='declared_reference',attention_ns_per_token=1000,
            pack_ns_per_token=30,expert_ns_per_contribution=100,merge_ns_per_token=30))
    protocol=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=9000,chunk_bytes=36000,
        ack_interval_bytes=1,base_rtt_ns=14000,switch_buffer_bytes=8388608,seed=11,
        ecn_by_rate={'400000000000':dict(kmin_bytes=131072,kmax_bytes=524288,pmax=1.0)},
        limits=dict(max_gpus=1024,max_switches=64,max_links=2048,max_messages=16384,max_queue_events=1,
            queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,rss_bytes=6*1024**3,
            address_bytes=12*1024**3,artifact_bytes=64*1024**2,result_json_bytes=32*1024**2))
    for name,value in [('topology',topology),('service',service),('protocol',protocol)]:save(output/(name+'.json'),value)
    save(output/'preparation.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        inputs={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.json')},
        protocol_sha256=hashlib.sha256((Path(__file__).with_name('host_scale_protocol.md')).read_bytes()).hexdigest(),
        preparation_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        workload='one finite synthetic layer, two decode steps; 256 EP4 replicas sharing one 1024GPU fabric'))


def analyze(run,output):
    # Only public stable reader; no private generators, test helpers, or truth.
    from moe_ub_cosim.host_timing import read_trace
    output.mkdir(exist_ok=False);traces={};missing=[]
    for writer in ('worker','native'):
        path=run/(writer+'_host.jsonl')
        if path.exists():traces[writer]=read_trace(path)
        else:missing.append(writer)
    result_path=run/'result.json'
    if not result_path.exists():
        for trace in traces.values():trace.pop('completed_spans')
        save(output/'analysis.json',dict(completed=False,traces=traces,missing_writers=missing,
            scope='saved prefix only; no invented completion/duration'))
        return
    result=read(result_path);config=read(run/'input.json');topology=read(run/'topology.json')
    groups=config['parallel']['groups'];assert len(groups)==256
    assert sorted(r for g in groups for r in g)==list(range(1024))
    kinds=Counter(node['kind'] for node in topology['nodes'])
    assert kinds['gpu']==1024 and kinds['tor']==32 and kinds['agg']==16 and kinds['core']==2
    flows={row['id']:row for row in result['messages']};callbacks={row['id']:row for row in result['service']['message_callbacks']}
    seen=set();coverage=set();network_bytes=local_bytes=0;contributions=0;remote_contributions=0
    server={n['id']:n['server'] for n in topology['nodes'] if n['kind']=='gpu'}
    for path in sorted((run/'graphs').glob('*/wave.json')):
        wave=read(path);group=wave['rank_ids'];graph=wave['graph_id']
        # Workload inputs are audited from the actual frontend export, not regenerated random seeds.
        assert len(wave['routes'])==1 and wave['routes'][0]['layer']==0
        selected=wave['routes'][0]['tokens'];expected=Counter()
        assert sorted(t['rank'] for t in selected)==group
        index,step=wave['group_index'],wave['decode_step']
        assert group==groups[index] and step in (0,1) and (index,step) not in coverage
        for token in selected:
            src=token['rank'];assert src in group and token['slot']==0 and len(set(token['experts']))==2
            for expert in token['experts']:
                dst=group[expert//4];contributions+=1
                if src==dst:continue
                remote_contributions+=1;expected['dispatch',src,dst]+=2048;expected['combine',dst,src]+=2048
        actual=Counter()
        for msg in wave['messages']:
            identity=msg['id'];assert identity not in seen;seen.add(identity)
            actual[msg['phase'].lower(),msg['src'],msg['dst']]+=msg['bytes']
            flow=flows[identity];cb=callbacks[identity]
            assert flow['bytes']==msg['bytes'] and flow['ack_callbacks']==1
            assert cb['send_callback_ns'] is not None and cb['recv_callback_ns'] is not None
            if server[msg['src']]==server[msg['dst']]:local_bytes+=msg['bytes']
            else:network_bytes+=msg['bytes']
        assert actual==expected
        # Graph identity is public and uniquely identifies replica/Decode wave.
        coverage.add((index,step))
    assert len(coverage)==512 and len(seen)==len(flows)==len(callbacks)
    assert coverage=={(index,step) for index in range(256) for step in range(2)}
    assert contributions==4096 and remote_contributions*2048*2==network_bytes+local_bytes
    assert result['passed'] and result['service']['service_finished'] and len(result['service']['waves'])==512
    assert not missing and all(t['trace_complete'] for t in traces.values())
    final=next(s['values'] for s in traces['native']['snapshots'] if s['name']=='message_resources_final')
    assert all(final[k]==len(flows) for k in ('registered_messages','send_posts','recv_posts','send_callbacks','recv_callbacks'))
    assert final['outstanding_send_callbacks']==final['outstanding_recv_callbacks']==0
    for trace in traces.values():
        root=next(s for s in trace['completed_spans'] if s['parent']==0)
        assert sum(s['exclusive_wall_ns'] for s in trace['completed_spans'])==root['inclusive_wall_ns']
        trace.pop('completed_spans')
    save(output/'analysis.json',dict(completed=True,independent_payload_audit=True,physical_nodes=dict(kinds),
        physical_links=len(topology['links']),groups=len(groups),graphs=len(coverage),messages=len(flows),
        expert_contributions=contributions,remote_contributions=remote_contributions,
        cross_server_payload_bytes=network_bytes,intra_host_payload_bytes=local_bytes,
        finish_ns=result['finish_ns'],final_counters=final,traces=traces,
        result_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
        scope='single synthetic short run; no full-model, speedup, modern-library or calibration claim'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--output',required=True,type=Path)
    p=sub.add_parser('analyze');p.add_argument('--run',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.output)
    else:analyze(args.run,args.output)
