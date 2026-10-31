"""Prepare two artificial complete EP2 groups on a128-endpoint Clos."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference-events',type=int,required=True)
    args=parser.parse_args()
    if not 1<=args.reference_events<=200000:parser.error('--reference-events must be1..200000')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    load=lambda path:json.loads((ROOT/path).read_bytes())
    save=lambda path,value:path.write_text(json.dumps(value,indent=2)+'\n')
    topology=load('examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type']='NONE';topology['intra_host']['enabled']=False
    save(out/'topology-config.json',topology)
    protocol=load('examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=1024,chunk_bytes=4096,switch_counter_storage='port_dense',queue_metrics=False,
        packet_capture=dict(nodes=[0,4,64,68],start_ns=0,end_ns=5000000,max_events=200000,wire_headers=True))
    protocol['limits'].update(max_messages=65536,max_queue_events=2000000,queue_capture='summary',max_sim_ns=5000000,
        wall_seconds=120,rss_bytes=2*2**30,address_bytes=4*2**30,artifact_bytes=512*2**20,result_json_bytes=256*2**20)
    save(out/'normal.json',protocol);fault=deepcopy(protocol)
    # Stream6 is the first group's rank1->rank0/empty local-expert1 count.
    fault['faults']=[dict(node=0,port=1,message_id=6,kind='data',sequence=0,start_ns=0,end_ns=5000000,count=1)]
    save(out/'fault.json',fault)
    source=load('examples/scaleout_rdma_transactions/selective-chain.json')
    transport={key:source[key] for key in ('transport_recovery','dcqcn_reference')}
    transport['dcqcn_reference']['max_events']=args.reference_events
    transport['transport_recovery'].update(initial_rto_ns=17000,max_events=500000,inflight_bytes=4096,receive_buffer_bytes=8192)
    save(out/'transport.json',transport)
    single_transport=deepcopy(transport);single_transport['dcqcn_reference']['max_events']=min(100000,args.reference_events)
    save(out/'transport-single.json',single_transport)
    groups=[]
    for index,(mapping,tokens) in enumerate([([0,64],[[[2],[3]],[[0]]]),([4,68],[[[0],[2]],[[1]]])]):
        path=out/f'group-{index}';path.mkdir()
        config=load('examples/deepep_indexed/input.json')
        config['model']=dict(experts=4,top_k=1,hidden_size=128,dispatch_format='bf16')
        config['execution'].update(ranks=2,max_tokens_per_rank=4)
        config['calls']=[dict(name='artificial-'+str(index),tokens=tokens)]
        save(path/'input.json',config)
        binding=dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=mapping,queue_depth=32,
            publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1')
        save(path/'binding.json',binding)
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','deepep-plan','--format','indexed-jsonl',
            '--config',str(path/'input.json'),'--output',str(path/'plan')],cwd=ROOT,check=True)
        groups.append(dict(id='group-'+str(index),plan=f'group-{index}/plan',binding=f'group-{index}/binding.json'))
    for name,selected in [('single.json',groups[:1]),('shared.json',groups)]:
        save(out/name,dict(schema_version='moe-legacy-shared-input/1',groups=selected))
    subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology','--config',str(out/'topology-config.json'),
        '--output',str(out/'topology')],cwd=ROOT,check=True)
    print(json.dumps(dict(prepared=True,network_endpoints=128,ep_groups=2,ep_size=2,experts_per_group=4,
        synthetic_tokens=6,real_routes=False,native_network_executed=False)))


if __name__=='__main__':main()
