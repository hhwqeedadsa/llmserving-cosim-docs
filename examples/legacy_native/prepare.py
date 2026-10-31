"""Prepare complete EP32/E256 artificial work on a128-endpoint Clos."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reference-events',type=int,required=True,
                        help='Explicit DCQCN log capacity, 1..100000; no automatic increase after failure')
    args=parser.parse_args()
    if not 1<=args.reference_events<=100000:parser.error('--reference-events must be 1..100000')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    load=lambda name:json.loads((ROOT/name).read_bytes())
    topology=load('examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type']='NONE';topology['intra_host']['enabled']=False
    protocol=load('examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=1024,chunk_bytes=4096,switch_counter_storage='port_dense',queue_metrics=False,
        packet_capture=dict(nodes=list(range(0,128,4)),start_ns=0,end_ns=5000000,max_events=1000000,wire_headers=True))
    protocol['limits'].update(max_messages=65536,max_queue_events=2000000,queue_capture='summary',
        max_sim_ns=5000000,wall_seconds=120,rss_bytes=2*2**30,address_bytes=4*2**30,
        artifact_bytes=512*2**20,result_json_bytes=256*2**20)
    fault=deepcopy(protocol)
    fault['faults']=[dict(node=4,port=1,message_id=0,kind='data',sequence=0,start_ns=0,end_ns=5000000,count=1)]
    source=load('examples/scaleout_rdma_transactions/selective-chain.json')
    transport={k:source[k] for k in ('transport_recovery','dcqcn_reference')}
    transport['dcqcn_reference']['max_events']=args.reference_events
    transport['transport_recovery'].update(initial_rto_ns=17000,max_events=500000,inflight_bytes=4096,receive_buffer_bytes=8192)
    binding=dict(schema_version='moe-legacy-native-binding/1',rank_to_gpu=list(range(0,128,4)),queue_depth=32,
        publication='legacy_sync_batch4/1',memory_model='single_span_zero_cost_visibility_expert_copy/1')
    for name,value in [('topology-config.json',topology),('normal.json',protocol),('fault.json',fault),
                       ('transport.json',transport),('binding.json',binding)]:
        (out/name).write_text(json.dumps(value,indent=2)+'\n')
    shutil.copyfile(ROOT/'examples/deepep_indexed/input.json',out/'plan-input.json')
    for arguments in [('topology','--config',str(out/'topology-config.json'),'--output',str(out/'topology')),
                      ('deepep-plan','--format','indexed-jsonl','--config',str(out/'plan-input.json'),'--output',str(out/'plan'))]:
        subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout',*arguments],cwd=ROOT,check=True)
    print(json.dumps(dict(prepared=True,endpoints=128,ep_size=32,experts_per_rank=8,
        data_source_ranks=[0,8,16,24],distinct_artificial_tokens=4,
        reference_event_capacity=args.reference_events,real_routes=False,network_executed=False)))


if __name__=='__main__':main()
