"""Prepare bounded synthetic incast and ASTRA drop-observation examples."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def prepare(output):
    output.mkdir(parents=True, exist_ok=False)
    def read(path):
        return json.loads((ROOT/path).read_text())
    def save(name,value):
        (output/name).write_text(json.dumps(value,indent=2)+'\n')
    def bounded(protocol):
        protocol['schema_version']='moe-scaleout-rdma/2'
        protocol['limits']=dict(max_gpus=32,max_switches=16,max_links=256,max_messages=8192,
            max_queue_events=200000,queue_capture='events',max_sim_ns=5000000,
            wall_seconds=60,rss_bytes=1073741824,address_bytes=2147483648,
            artifact_bytes=67108864,result_json_bytes=16777216)
        return protocol
    topology=read('examples/scaleout_rdma/small-clos-100g.json')
    for tier in ('tor_agg','agg_core','tor_core'):
        topology['links'][tier]['rate_bps']=25000000000
    protocol=bounded(read('examples/astra_stream_service/rdma.json'))
    protocol['switch_counter_storage']='port_dense'
    protocol['switch_buffer_bytes']=1048576
    protocol['payload_bytes']=9000
    protocol['chunk_bytes']=9000
    protocol['ecn_by_rate']={'100000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0),
                             '25000000000':dict(kmin_bytes=16384,kmax_bytes=65536,pmax=1.0)}
    protocol['queue_metrics']=True
    protocol['recovery']=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=4,max_events=100000)
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    save('topology.json',topology)
    save('messages.json',[dict(id=i,src=i,dst=7,bytes=262144,plane=0,ready_ns=0) for i in range(7)])
    save('off.json',protocol)
    capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    protocol['switch_drop_capture']=capture
    save('on.json',protocol)
    service_protocol=bounded(read('examples/astra_stream_service/rdma.json'))
    service_protocol['switch_drop_capture']=capture
    save('service-rdma.json',service_protocol)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    prepare(parser.parse_args().output)
