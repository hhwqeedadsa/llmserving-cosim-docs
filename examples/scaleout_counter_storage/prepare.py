"""Prepare bounded counter-storage functionality inputs, without network execution."""
import argparse
from copy import deepcopy
import importlib.util
import json
from pathlib import Path


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')


def prepare(repo, output):
    output.mkdir()
    load=lambda name:json.loads((repo/name).read_text())
    small=load('examples/scaleout_multirail_native/topology.json')
    protocol=load('examples/scaleout_multirail_native/rdma.json')
    for ecn in protocol['ecn_by_rate'].values():
        ecn.update(kmin_bytes=4096,kmax_bytes=8192)
    protocol['packet_capture'].update(max_events=100000)
    work=load('examples/scaleout_multirail_native/work.json')
    for item in work['work']:item['bytes']=65536
    save(output/'small-topology.json',small)
    save(output/'small-work.json',work)
    save(output/'small-default.json',protocol)
    for mode in ('legacy_dense','port_dense'):
        save(output/('small-'+mode+'.json'),dict(protocol,switch_counter_storage=mode))
    # Shared Agg--Core is the sole inter-pod path. This intentionally allows
    # bounded partition failure; the condition is not tuned to obtain recovery.
    from moe_ub_cosim.scaleout_topology import generate
    physical,_=generate(small)
    edge=next(link for link in physical['links'] if link['tier']=='agg_core')
    failure=dict(mode='switch_links/1',events=[dict(src=edge['src'],dst=edge['dst'],
        time_ns=12000,route_update_delay_ns=2000)])
    for mode in ('legacy_dense','port_dense'):
        save(output/('failure-'+mode+'.json'),dict(protocol,switch_counter_storage=mode,link_failures=failure))
    failed_work=deepcopy(work)
    failed_work['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=100000,
        max_rto_ns=400000,max_retries=4,max_events=10000)
    save(output/'failure-work.json',failed_work)
    large=load('examples/scaleout_rdma/small-clos-100g.json')
    large['deployment'].update(pods=8,servers_per_segment=16,gpus_per_server=8,gpu_type='NONE')
    large['nic'].update(rails_by_gpu_slot=[[0] for _ in range(8)],total_rate_bps=200000000000)
    for link in large['links'].values():link.update(rate_bps=200000000000,delay_ns=2000)
    save(output/'large-topology.json',large)
    spec=importlib.util.spec_from_file_location('public_ep_groups',repo/'examples/scaleout_rdma_streams/ep_groups.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    save(output/'large-work.json',module.program(1024,4,1,384,200))
    limits=dict(max_gpus=1024,max_switches=64,max_links=2048,max_messages=65536,
        max_queue_events=200000,queue_capture='summary',max_sim_ns=5000000,
        wall_seconds=60,rss_bytes=4*2**30,address_bytes=8*2**30,
        artifact_bytes=64*2**20,result_json_bytes=32*2**20)
    large_protocol=load('examples/scaleout_rdma/rdma.json')
    large_protocol.update(schema_version='moe-scaleout-rdma/2',limits=limits,queue_metrics=True,
        base_rtt_ns=28000,ecn_by_rate={'200000000000':dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0)},
        packet_capture=dict(nodes=list(range(1024)),start_ns=0,end_ns=5000000,max_events=100000,wire_headers=True))
    for mode in ('legacy_dense','port_dense'):
        save(output/('large-'+mode+'.json'),dict(large_protocol,switch_counter_storage=mode))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    prepare(a.repo.resolve(),a.output.resolve())
