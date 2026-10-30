"""Self-contained 128-card Clos input with deliberately colocated experts.

Four distinct synthetic originals check functionality, not R1 performance.
"""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.service_transport import message_preflight
from moe_ub_cosim.token_transport import verify_wave

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output, combine, reduce_cost):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    t = json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes())
    t['intra_host']['enabled'] = False
    topology, report = generate(t)
    folder = output/'topology'
    folder.mkdir()
    save(output/'topology-config.json', t)
    save(folder/'topology.json', topology)
    save(folder/'report.json', report)
    manifest = json.loads((ROOT/'examples/scaleout_route_replay/manifest.json').read_bytes())
    name = 'synthetic/direct-token-H256-E256-K8'
    manifest['source'].update(dataset_id='repo/scaleout-token-transport', dataset_revision='example-v1',
        collector_model_id=name, source_url='examples/scaleout_token_transport')
    manifest['model'].update(name=name, num_layers=2)
    manifest['layers'] = [dict(layer_id=l, kind='moe', num_experts=256, top_k=8) for l in range(2)]
    manifest['files'] = []
    sources = output/'sources'
    sources.mkdir()
    for request in range(4):
        raw = [{str(layer): [[(request*28+token*8+entry*12+layer*16+j)%256
                              for j in range(8)] for token in range(16+request if entry == 0 else 1)]
                for layer in range(2)} for entry in range(3)]
        path = sources/f'{request}.json'
        save(path, raw)
        manifest['files'].append(dict(file_id=f'synthetic-{request}', original_path=f'synthetic/direct/{request}.json',
            file=dict(path=str(path), sha256=sha(path)), format='token_layer_map_v1'))
    save(output/'manifest.json', manifest)
    c = json.loads((ROOT/'examples/astra_stream_service/service.json').read_bytes())
    c['model'].update(name=name, experts=256, top_k=8, gpu_type='H100')
    c['parallel']['groups'] = [list(range(0, 128, 2)), list(range(1, 128, 2))]
    c['phase_completion'] = dict(mode='ep_transport_flush/1')
    c['token_transport'] = dict(mode='deepep_direct_bf16/1', combine=combine, local_reduce_ns_per_extra=reduce_cost)
    c['route_replay'].update(request_layout='sparse_explicit/1',
        manifest=dict(path=str(output/'manifest.json'), sha256=sha(output/'manifest.json')), layer_ids=[0, 1],
        requests=[dict(rank_id=rank, slot=0, file_id=f'synthetic-{i}', first_decode_entry=1)
                  for i, rank in enumerate((0, 1, 64, 65))])
    replay = Replay(c, ROOT)
    waves = [make_wave(c, group, index, step, 1, replay)
             for index, group in enumerate(c['parallel']['groups']) for step in range(2)]
    for wave in waves:
        verify_wave(wave, c)
    keys = sorted({(m['src'], m['dst'], m['plane']) for w in waves for m in w['messages']})
    transport = c['transport']
    transport['streams'] = [dict(id=i, src=s, dst=d, plane=p, wire_port=21000+i,
        source_nic=1, destination_nic=1, queue_depth=32) for i, (s, d, p) in enumerate(keys)]
    transport['limits'].update(max_streams=len(keys), max_work=512, max_link_transmissions=200000)
    transport['transport_recovery'].update(inflight_bytes=2**20, receive_buffer_bytes=2**20+8999, max_events=30000)
    transport['dcqcn_reference']['max_events'] = 30000
    p = json.loads((ROOT/'examples/astra_stream_service/rdma.json').read_bytes())
    p.update(schema_version='moe-scaleout-rdma/2', payload_bytes=9000, chunk_bytes=9000,
        switch_counter_storage='port_dense',
        limits=dict(max_gpus=128, max_switches=128, max_links=1024, max_messages=512,
            max_queue_events=200000, queue_capture='summary', max_sim_ns=5000000, wall_seconds=60,
            rss_bytes=2**30, address_bytes=2**31, artifact_bytes=128*2**20, result_json_bytes=32*2**20))
    p['packet_capture']['max_events'] = 200000
    p['ecn_by_rate'] = {'400000000000': dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)}
    validate(c, topology, p, replay)
    preflight = message_preflight(topology, p, c)
    for wave in waves:
        preflight.accept_wave(wave, 0)
    save(output/'service.json', c)
    save(output/'rdma.json', p)
    save(output/'preparation.json', dict(passed=True, network_executed=False, gpu_endpoints=128,
        ep_sizes=[64, 64], requests=4, synthetic=True, combine=combine,
        messages=sum(len(w['messages']) for w in waves),
        layout_payload_bytes=sum(m['bytes'] for w in waves for m in w['messages']),
        expert_contributions=sum(sum(l['expert_counts'].values()) for w in waves for l in w['routes']),
        local_reduce_declared_ns=sum(n['duration_ns'] for w in waves for ns in w['rank_nodes'].values()
            for n in ns if n['name'].endswith('combine_reduce'))))
    print(json.dumps(dict(passed=True, gpu_endpoints=128, combine=combine, streams=len(keys))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--combine', choices=('rank_reduced/1', 'expanded_per_expert/1'), required=True)
    parser.add_argument('--local-reduce-ns-per-extra', type=int, required=True)
    args = parser.parse_args()
    prepare(args.output, args.combine, args.local_reduce_ns_per_extra)
