"""Freeze 15 independent real routes on one 128-endpoint shared Clos fabric."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave, validate
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.service_transport import message_preflight

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
GROUPS = [list(range(0, 128, 2)), list(range(1, 128, 2))]


def load(path):
    if path.stat().st_size > 64 * 2**20:
        raise ValueError('JSON input exceeds 64MiB: ' + str(path))
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def prepare(intake_path, routes, output):
    output.mkdir(parents=True, exist_ok=False)
    originals = output / 'originals'
    originals.mkdir()
    intake = load(intake_path)
    selected = sorted((row for row in intake['files'] if row['eligible_offline']),
                      key=lambda row: int(Path(row['name']).stem))
    if len(selected) != 15 or len({r['sha256'] for r in selected}) != 15:
        raise ValueError('this registered study requires the 15 distinct eligible originals')
    manifest = load(ROOT / 'experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json')
    previous_sources = {(row['file']['sha256'], row['original_path']) for row in manifest['files']}
    manifest['source'].update(kind='user_provided_model_routes', dataset_revision=None,
        source_url='https://huggingface.co/datasets/core12345/MoE_expert_selection_trace/tree/main/cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        usage_terms='User confirmed model/task directory; local SHA-256 verified; remote revision/license not independently verified.')
    manifest['source']['unknown_fields'] = sorted(set(manifest['source']['unknown_fields']) | {'dataset_revision'})
    manifest['files'] = []
    source_rows = []
    requests = []
    for i, row in enumerate(selected):
        name = row['name']
        # Intake's flag covers the eleven additions. The original four have
        # their earlier user-confirmed identity in the existing manifest.
        confirmed = row['origin_directory_user_confirmed'] or (
            row['sha256'], row['original_path']) in previous_sources
        if Path(name).name != name or not confirmed:
            raise ValueError('unconfirmed or invalid source identity')
        path = routes / name
        if sha(path) != row['sha256']:
            raise ValueError('intake hash differs: ' + name)
        target = originals / name
        target.write_bytes(path.read_bytes())
        group_index, ordinal = i % 2, i // 2
        group = GROUPS[group_index]
        rank = group[ordinal * len(group) // (8 if group_index == 0 else 7)]
        file_id = 'local-' + path.stem
        manifest['files'].append(dict(file_id=file_id, original_path=row['original_path'],
            file=dict(path=str(target), sha256=sha(target)), format='token_layer_map_v1'))
        requests.append(dict(rank_id=rank, slot=0, file_id=file_id, first_decode_entry=1))
        source_rows.append(dict(name=name, file_id=file_id, sha256=sha(target), rank=rank,
                                group=group_index, original_path=row['original_path']))
    save(output / 'manifest.json', manifest)
    topology_config = load(ROOT / 'examples/scaleout_clos128/clos-128.json')
    topology_config['intra_host']['enabled'] = False
    topology, report = generate(topology_config)
    save(output / 'topology-config.json', topology_config)
    folder = output / 'topology'
    folder.mkdir()
    save(folder / 'topology.json', topology)
    save(folder / 'report.json', report)
    config = load(ROOT / 'examples/astra_stream_service/service.json')
    config['model'].update(name='deepseek-ai/DeepSeek-R1', hidden_size=7168,
        ffn_hidden_size=2048, experts=256, top_k=8, gpu_type='H100')
    config['workload'].update(batch_per_rank=1, context_tokens=2048)
    config['parallel']['groups'] = deepcopy(GROUPS)
    config['phase_completion'] = dict(mode='ep_transport_flush/1')
    config['route_replay'].update(request_layout='sparse_explicit/1',
        manifest=dict(path=str(output / 'manifest.json'), sha256=sha(output / 'manifest.json')),
        layer_ids=[3, 4], requests=requests, budget=dict(max_files=15, max_bytes=64 * 2**20))
    replay = Replay(config, ROOT)
    waves = [make_wave(config, group, gi, step, 1, replay)
             for gi, group in enumerate(GROUPS) for step in range(2)]
    keys = sorted({(m['src'], m['dst'], m['plane']) for wave in waves for m in wave['messages']})
    transport = config['transport']
    transport['streams'] = [dict(id=i, src=s, dst=d, plane=p, wire_port=21000+i,
        source_nic=1, destination_nic=1, queue_depth=32) for i, (s, d, p) in enumerate(keys)]
    transport['limits'].update(max_streams=len(keys), max_work=2048, max_link_transmissions=200000)
    transport['transport_recovery'].update(inflight_bytes=2**20,
        receive_buffer_bytes=2**20+8999, max_events=30000)
    transport['dcqcn_reference']['max_events'] = 30000
    transport['source_rate_capture'] = dict(start_ns=0, end_ns=5000000, max_events=30000)
    protocol = load(ROOT / 'examples/astra_stream_service/rdma.json')
    protocol.update(schema_version='moe-scaleout-rdma/2', payload_bytes=9000, chunk_bytes=9000,
        switch_counter_storage='port_dense', queue_metrics=True,
        switch_drop_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=10000),
        limits=dict(max_gpus=128, max_switches=128, max_links=1024, max_messages=2048,
            max_queue_events=200000, queue_capture='summary', max_sim_ns=5000000,
            wall_seconds=60, rss_bytes=2**30, address_bytes=2**31,
            artifact_bytes=128 * 2**20, result_json_bytes=32 * 2**20))
    protocol['packet_capture']['max_events'] = 200000
    protocol['ecn_by_rate'] = {'400000000000': dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)}
    planned = output / 'planned'
    planned.mkdir()
    totals = Counter()
    incoming = Counter({rank: 0 for rank in GROUPS[0]})
    duplicate_remote_contributions = 0
    for wave in waves:
        gi, step = wave['group_index'], wave['decode_step']
        expected = Counter()
        for request in requests:
            src = request['rank_id']
            if src not in GROUPS[gi]:
                continue
            original = load(originals / (request['file_id'][6:] + '.json'))
            for layer, lid in enumerate((3, 4)):
                destinations = [GROUPS[gi][e // 4] for e in original[step+1][str(lid)][0]]
                remote = [dst for dst in destinations if dst != src]
                duplicate_remote_contributions += len(remote) - len(set(remote))
                for dst in remote:
                    expected[layer, 'dispatch', src, dst] += 14336
                    expected[layer, 'combine', dst, src] += 14336
        actual = {(m['layer'], m['phase'], m['src'], m['dst']): m['bytes'] for m in wave['messages']}
        if actual != dict(expected) or len(actual) != len(wave['messages']):
            raise ValueError('independent original expert contribution matrix differs')
        for m in wave['messages']:
            totals[m['src'], m['dst'], m['plane']] += m['bytes']
            if gi == 0 and step == 0 and m['layer'] == 0 and m['phase'] == 'dispatch':
                incoming[m['dst']] += m['bytes']
        save(planned / (wave['graph_id'] + '.json'), wave)
    if max(totals.values()) >= 2**20:
        raise ValueError('SR window could constrain this workload')
    hotspot = min(GROUPS[0], key=lambda r: (-incoming[r], r))
    for case in ('normal', 'local'):
        p = deepcopy(protocol)
        if case == 'local':
            p['data_loss'] = dict(kind='periodic_destination_data/1', numerator=1, denominator=1,
                offset=0, start_ns=0, end_ns=100000, nodes=[hotspot])
        validate(config, topology, p, replay)
        admission = message_preflight(topology, p, config)
        for wave in waves:
            admission.accept_wave(wave, 0)
        save(output / (case + '-rdma.json'), p)
    save(output / 'service.json', config)
    summary = dict(schema_version='moe-clos-transient-preparation/1', passed=True,
        platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        gpu_endpoints=128, ep_sizes=[64, 64], experts_per_rank_per_layer=4,
        independent_requests=15, requests_per_group=[8, 7], counts=report['counts'],
        streams=len(keys), messages=sum(len(w['messages']) for w in waves),
        logical_bytes=sum(totals.values()), max_planned_stream_bytes=max(totals.values()),
        sr_window_bytes=2**20, sr_total_configured_receiver_capacity=len(keys)*(2**20+8999),
        hotspot_rank=hotspot, first_dispatch_incoming_bytes=dict(incoming), sources=source_rows,
        original_contributions_with_repeated_destination=duplicate_remote_contributions,
        peer_deduplication_modeled=False, hardware_calibrated=False, network_executed=False,
        method_hashes={p.name: sha(p) for p in HERE.iterdir() if p.suffix in ('.py', '.md')},
        intake_sha256=sha(intake_path),
        input_hashes={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file()})
    save(output / 'preparation.json', summary)
    print(json.dumps({k: summary[k] for k in ('passed', 'gpu_endpoints', 'ep_sizes',
        'independent_requests', 'streams', 'messages', 'logical_bytes', 'hotspot_rank')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('intake', 'routes-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    prepare(args.intake.resolve(), args.routes_root.resolve(), args.output.resolve())
