"""Freeze the same source-backed Dispatch window for four transport conditions."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    root = a.repository.resolve()
    manifest = json.loads((root / 'data/moe-trace-small/manifest.json').read_text())
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert len(manifest['files']) == 4
    routes, work, counts = [], [], []
    for rank, entry in enumerate(manifest['files']):
        path = root / entry['file']['path']
        assert path.stat().st_size < 4 * 2**20 and sha(path) == entry['file']['sha256']
        raw = json.loads(path.read_text())
        tokens = raw[0]['4'][:8]
        counts.append(len(tokens))
        routes.append(dict(file_id=entry['file_id'], sha256=sha(path), tokens=tokens))
        for token, experts in enumerate(tokens):
            assert len(experts) == len(set(experts)) == 8
            assert all(type(e) is int and 0 <= e < 256 for e in experts)
            for dst in sorted({e // 64 for e in experts} - {rank}):
                work.append(dict(id=len(work), stream=rank * 4 + dst, bytes=14464, after=[]))
    total = Counter()
    for w in work:
        total[w['stream']] += w['bytes']
    assert counts == [8, 8, 8, 6] and len(work) == 74 and max(total.values()) == 115712
    a.output.mkdir()
    observed, truth = a.output / 'observed', a.output / 'truth'
    observed.mkdir(); truth.mkdir()
    save(truth / 'routes.json', dict(manifest=manifest, selection=dict(entry=0, layer=4, cap=8), routes=routes))
    topology = dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2, segments_per_pod=1, servers_per_segment=2, gpus_per_server=2, gpu_type='NONE'),
        fabric=dict(rails=1, planes=1, tors_per_segment_per_rail=2, upper_tier_scope='shared',
                    aggs_per_pod_per_group=2, cores_per_group=2, tor_uplinks=2, agg_uplinks=1, core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0], [0]], total_rate_bps=100000000000),
        links={tier: dict(rate_bps=100000000000 if tier == 'access' else 25000000000, delay_ns=1000)
               for tier in ('access', 'tor_agg', 'agg_core', 'tor_core')},
        port_limits=dict(gpu=16, tor=128, agg=128, core=128),
        budget=dict(max_nodes=8192, max_links=131072, max_switches=1024))
    save(observed / 'topology.json', topology)
    protocol = dict(schema_version='moe-scaleout-rdma/2', cc_mode=1, payload_bytes=1000, chunk_bytes=4000,
        ack_interval_bytes=1, base_rtt_ns=14000, switch_buffer_bytes=8388608, seed=11,
        ecn_by_rate={str(rate): dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0)
                     for rate in (100000000000, 25000000000)}, queue_metrics=True,
        packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=100000, wire_headers=True),
        mmu_snapshot=dict(nodes=list(range(8, 18)), max_ports=128),
        limits=dict(max_gpus=32, max_switches=16, max_links=256, max_messages=8192,
                    max_queue_events=200000, queue_capture='summary', max_sim_ns=5000000,
                    wall_seconds=60, rss_bytes=2**30, address_bytes=2**31,
                    artifact_bytes=64 * 2**20, result_json_bytes=16 * 2**20))
    save(observed / 'healthy-protocol.json', protocol)
    failed = deepcopy(protocol)
    failed['link_failures'] = dict(mode='switch_links/1', events=[dict(src=8, dst=10, time_ns=4000, route_update_delay_ns=12000)])
    save(observed / 'failed-protocol.json', failed)
    config = dict(schema_version='moe-rdma-stream-work/2', limits=dict(max_streams=12, max_work=96),
        streams=[dict(id=s*4+d, src=s*2, dst=d*2, plane=0, wire_port=21000+s*4+d,
                      source_nic=1, destination_nic=1, queue_depth=1024)
                 for s in range(4) for d in range(4) if s != d], work=work,
        transport_recovery=dict(mode='go_back_n/1', initial_rto_ns=80000, max_rto_ns=640000, max_retries=3, max_events=10000),
        source_rate_capture=dict(start_ns=0, end_ns=5000000, max_events=20000),
        dcqcn_reference=dict(mode='figure7_reference/1', np_interval_ns=50000, alpha_interval_ns=55000,
            recovery_interval_ns=55000, recovery_bytes=10000000, min_rate_bps=100000000,
            additive_bps=40000000, hyper_bps=50000000, fast_recovery_rounds=5, gain=1/256,
            cnp_model_bytes=48, max_events=20000))
    save(observed / 'gbn-work.json', config)
    selective = deepcopy(config)
    selective['transport_recovery'].update(mode='selective_repeat/1', receive_buffer_bytes=262144, inflight_bytes=261145)
    save(observed / 'selective-work.json', selective)
    cases = [dict(name=f'{net}-{mode}', network=net, mode=mode, protocol=f'{net}-protocol.json', work=f'{mode}-work.json')
             for mode in ('gbn', 'selective') for net in ('healthy', 'failed')]
    save(observed / 'index.json', dict(schema_version='selective-externality/1',
        platform_commit='2dc9eb71a801eb88e8757bb33324e8b2729ab2d3', cases=cases,
        selected_tokens=counts, work_count=len(work), payload_bytes=sum(w['bytes'] for w in work),
        stream_unique_bytes=dict(total), receive_payload_budget_bytes_per_stream=262144,
        window_never_binding=all(n < 261145 for n in total.values()),
        files={p.name: sha(p) for p in sorted(observed.iterdir())},
        complete_inference=False, hardware_calibrated=False, independent_heldout=False))
    print(json.dumps(dict(tokens=counts, work=len(work), bytes=sum(w['bytes'] for w in work), max_stream_bytes=max(total.values()))))


if __name__ == '__main__':
    main()
