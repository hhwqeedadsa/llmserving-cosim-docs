"""Four connections on one shared fabric and shared target bytes; no simulation."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from moe_ub_cosim.scaleout_topology import execute


def prepare(output):
    output=Path(output).resolve();output.mkdir(exist_ok=False)
    def save(name,value):
        (output/name).write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
    topology=dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2,segments_per_pod=1,servers_per_segment=2,gpus_per_server=1,gpu_type='NONE'),
        fabric=dict(rails=1,planes=1,tors_per_segment_per_rail=1,upper_tier_scope='shared',
                    aggs_per_pod_per_group=1,cores_per_group=1,tor_uplinks=1,agg_uplinks=1,core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0]],total_rate_bps=100000000000),
        links={key:dict(rate_bps=100000000000,delay_ns=1000) for key in ('access','tor_agg','agg_core','tor_core')},
        port_limits=dict(gpu=16,tor=128,agg=128,core=128),budget=dict(max_nodes=32,max_links=64,max_switches=16))
    save('topology-config.json',topology);execute(output/'topology-config.json',output/'topology')
    protocol=dict(schema_version='moe-scaleout-rdma/2',cc_mode=1,payload_bytes=256,chunk_bytes=256,
        ack_interval_bytes=1,base_rtt_ns=14000,switch_buffer_bytes=8388608,switch_counter_storage='port_dense',
        ecn_by_rate={'100000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)},seed=11,
        packet_capture=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=80000,wire_headers=True),queue_metrics=True,
        limits=dict(max_gpus=4,max_switches=16,max_links=64,max_messages=64,max_queue_events=80000,
            queue_capture='events',max_sim_ns=5000000,wall_seconds=60,rss_bytes=1073741824,
            address_bytes=2147483648,artifact_bytes=67108864,result_json_bytes=33554432))
    save('rdma.json',protocol)
    memories=[]
    for gpu in range(4):
        data=bytearray(4096)
        if gpu==3:data[3072:3080]=(41).to_bytes(8,'little')
        if gpu==0:data[1024:1032]=(7).to_bytes(8,'little')
        memories.append(dict(gpu=gpu,data_hex=data.hex()))
    connections=[]
    for cid,src in enumerate((0,1,0)):
        connections.append(dict(id=cid,src=src,dst=3,source_nic=1,destination_nic=1,plane=0,operations=[
            dict(kind='write',address=cid*1024,data_hex=bytes((i+17*cid)%251 for i in range(600)).hex()),
            dict(kind='fetch_add',address=3072,addend=cid+1)]))
    connections.append(dict(id=3,src=3,dst=0,source_nic=1,destination_nic=1,plane=0,operations=[
        dict(kind='write',address=0,data_hex=bytes(range(32)).hex()),dict(kind='fetch_add',address=1024,addend=4)]))
    work=dict(schema_version='moe-rc-concurrent-input/1',mtu=256,rto_ns=100000,memories=memories,
              connections=connections,loss=None)
    for name,rule in [('healthy',None),('drop_write_middle',dict(connection=0,node=3,opcode=7,psn=1)),
                      ('drop_atomic_ack',dict(connection=1,node=1,opcode=18,psn=3))]:
        value=deepcopy(work);value['loss']=rule;save(name+'.json',value)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    print(prepare(parser.parse_args().output))
