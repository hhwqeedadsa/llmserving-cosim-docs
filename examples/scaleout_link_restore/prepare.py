"""Prepare an independent Clos128 functional example of switch-link restoration."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    load = lambda p: json.loads(p.read_bytes())
    topology = load(ROOT/'examples/scaleout_clos128/clos-128.json')
    topology['deployment']['gpu_type'] = 'NONE'
    topology['intra_host']['enabled'] = False
    for tier in ('tor_agg', 'agg_core', 'tor_core'):
        topology['links'][tier]['rate_bps'] = 100000000000
    protocol = load(ROOT/'examples/scaleout_clos128/rdma.json')
    protocol.update(payload_bytes=1000, chunk_bytes=4000, switch_counter_storage='port_dense',
        packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=200000, wire_headers=True),
        queue_metrics=True, mmu_snapshot=dict(nodes=[128, 132], max_ports=64))
    protocol['ecn_by_rate'] = {str(rate): dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)
                               for rate in (100000000000, 400000000000)}
    protocol['limits'].update(max_gpus=128, max_switches=32, max_links=1024, max_messages=128,
        max_queue_events=200000, queue_capture='events', max_sim_ns=5000000, wall_seconds=60,
        rss_bytes=2**30, address_bytes=2*2**30, artifact_bytes=128*2**20, result_json_bytes=64*2**20)
    work = load(ROOT/'examples/scaleout_rdma_streams/work-selective.json')
    # Fixed source/destination ports select Agg132 in the original native ECMP
    # table. This is explicit functional coverage, not representative MoE traffic.
    bindings = [(0, 8, 10001), (1, 9, 10003), (8, 0, 10002), (9, 1, 10007)]
    work['streams'] = [dict(id=i, src=s, dst=d, plane=0, wire_port=port, source_nic=1,
        destination_nic=1, queue_depth=2) for i, (s, d, port) in enumerate(bindings)]
    work['work'] = [dict(id=0, kind='local', rank=0, duration_ns=100000, after=[])]
    for i in range(4):
        work['work'].append(dict(id=1+i, kind='write', stream=i, bytes=64000, after=[]))
    for i in range(4):
        work['work'].append(dict(id=5+i, kind='write', stream=i, bytes=16000,
            after=[dict(work=0, event='completed'), dict(work=1+i, event='acked')]))
    work['limits'] = dict(max_streams=4, max_work=9)
    work['transport_recovery'].update(initial_rto_ns=17000, max_rto_ns=640000, max_retries=3,
                                     receive_buffer_bytes=65536, inflight_bytes=64000)
    protocols = dict(normal=deepcopy(protocol))
    for name, down, down_delay, up, up_delay, local in (
        ('stale-down', 4000, 296000, 24000, 0, False),
        ('ordered-local', 4000, 12000, 24000, 12000, True),
        ('physical-before-report', 4000, 16000, 10000, 20000, False),
        ('inflight', 1050, 0, 1051, 0, False),
    ):
        c = deepcopy(protocol)
        c['link_failures'] = dict(mode='switch_links/2', events=[dict(src=128, dst=132,
            time_ns=down, route_update_delay_ns=down_delay,
            restore_time_ns=up, restore_route_update_delay_ns=up_delay)])
        if local:
            c['local_failover'] = dict(mode='live_ecmp/1')
        protocols[name] = c
    return topology, protocols, work


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    topology, protocols, work = inputs()
    for name, value in [('topology-config.json', topology), ('work.json', work),
                        *[(n+'.json', v) for n, v in protocols.items()]]:
        with (out/name).open('x') as s:
            json.dump(value, s, indent=2)
            s.write('\n')
    subprocess.run([sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'topology',
        '--config', str(out/'topology-config.json'), '--output', str(out/'topology')], cwd=ROOT, check=True)
    from moe_ub_cosim.scaleout_streams import validate
    t = json.loads((out/'topology/topology.json').read_bytes())
    for c in protocols.values():
        validate(t, c, work)
    print(json.dumps(dict(prepared=True, network_executed=False, gpu_endpoints=128,
                         source='artificial functional byte workload', cases=list(protocols))))


if __name__ == '__main__':
    main()
