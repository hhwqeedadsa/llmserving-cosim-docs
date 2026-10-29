"""Bounded CPU-only counterexamples for the 2026-10-29 project review.

Reports current behavior; it is not a test requiring defects to stay present.
No test fixture driver, simulator, compiler, network or user route is executed.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_arrivals import reconstruct
from moe_ub_cosim.scaleout_rdma import validate_probe
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.simai_service import validate as validate_simai

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    return json.loads((ROOT / path).read_text())


def accepted(call):
    try:
        call()
    except (ValueError, KeyError) as error:
        return dict(accepted=False, reason=str(error))
    return dict(accepted=True)


def port_collision():
    topology, _ = generate(read('examples/scaleout_rdma/small-clos-100g.json'))
    protocol = read('examples/scaleout_rdma/rdma.json')
    messages = [dict(id=identity, src=0, dst=4, bytes=1024, plane=0, ready_ns=0)
                for identity in (0, 50000)]
    verdict = accepted(lambda: validate_probe(topology, protocol, messages))
    return dict(python_preflight=verdict, messages=messages,
                modeled_wire_ports=[10000 + m['id'] % 50000 for m in messages],
                native_guard='Fabric::Send port_to_flow_.emplace rejects repeated source/destination/port; '
                             'source-audited, not an executed native failure')


def admission_size():
    folder = ROOT / 'examples/simai_service'
    config = read('examples/simai_service/service.json')
    protocol = read('examples/simai_service/rdma.json')
    protocol['admission'] = dict(policy='fifo', max_active_per_source=1,
                                decision_ns=0, max_active_bytes_per_source=1)
    topology, _ = generate(read('examples/simai_service/topology.json'))
    verdict = accepted(lambda: validate_simai(config, topology, protocol, folder))
    if not verdict['accepted']:
        return dict(service_preflight=verdict)
    _, planes, routes = validate_simai(config, topology, protocol, folder)
    wave = make_wave(config['service'], config['service']['parallel']['groups'][0], 0, 0, planes, routes)
    message = next(m for m in wave['messages'] if m['bytes'] > 1)
    concrete = {k: message[k] for k in ('id', 'src', 'dst', 'bytes', 'plane')}
    concrete['ready_ns'] = 0
    return dict(service_preflight=verdict, generated_message=concrete,
                direct_message_preflight=accepted(lambda: validate_probe(topology, protocol, [concrete])),
                declared_source_byte_cap=1)


def arrival_time():
    columns = 'time_ns node port event packet_uid message_id ip_protocol sequence model_bytes ecn egress_queue_bytes data_payload_bytes'.split()
    events = [[1, 0, 1, 0, 7, 0, 17, 0, 142, 0, 0, 100],
              [2, 1, 1, 1, 7, 0, 17, 0, 142, 0, 0, 100],
              [4, 0, 1, 1, 8, 0, 252, 100, 48, 0, 0, 0]]
    result = dict(native_network_executed=True, service={}, passed=True, finish_ns=5, gpu_count=2,
                  rdma_parameters=dict(hosts=[dict(node=i, attributes=dict(L2BackToZero=False)) for i in (0, 1)]),
                  messages=[dict(id=0, src=0, dst=1, bytes=100, plane=0, source_data_packets=1,
                                 source_data_payload_bytes=100, receiver_contiguous_bytes=100,
                                 receive_ready_ns=2, ack_ns=4)],
                  packet_capture=dict(complete=True, omitted_events=0, columns=columns, events=events,
                                      selection=dict(start_ns=0, end_ns=6, nodes=[]),
                                      event_codes=dict(tx=0, rx=1, drop=2),
                                      protocol_codes=dict(data=17, ack=252, nack=253)))
    waves = [dict(graph_id='g0.w0', group_index=0, decode_step=0, routes=[dict(layer=0)],
                  messages=[dict(id=0, src=0, dst=1, bytes=100, plane=0, layer=0, phase='dispatch')])]
    baseline = reconstruct(result, waves, 'g0.w0', 0, 'dispatch')[0]
    changed = deepcopy(result)
    changed['messages'][0]['ack_ns'] = 1
    verdict = accepted(lambda: reconstruct(changed, waves, 'g0.w0', 0, 'dispatch'))
    output = reconstruct(changed, waves, 'g0.w0', 0, 'dispatch')[0] if verdict['accepted'] else None
    return dict(actual_complete_feedback_rx_ns=4, baseline_slice=baseline['slice'],
                altered_ack_ns=1, altered_audit=verdict,
                altered_slice=output['slice'] if output else None,
                scope='artificial saved-record mutation; no evidence that historical native outputs are wrong')


def review():
    sources = ['backend/rdma/fabric.cc', 'python/moe_ub_cosim/scaleout_rdma.py',
               'python/moe_ub_cosim/scaleout_service.py', 'python/moe_ub_cosim/simai_service.py',
               'python/moe_ub_cosim/scaleout_connectivity.py', 'python/moe_ub_cosim/scaleout_arrivals.py']
    return dict(schema_version='moe-comprehensive-review/1', network_runs=0, builds=0,
                sources={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
                cases=dict(wire_port_collision=port_collision(), admission_message_size=admission_size(),
                           source_confirmation_time=arrival_time()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New JSON file; no overwrite')
    args = parser.parse_args()
    value = review()
    with args.output.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
