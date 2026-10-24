"""Prepare frozen saved-DAG inputs; original routes are evaluation-only truth."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess


def load(p): return json.loads(p.read_text())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def project(obj):
    if isinstance(obj, list): return [project(v) for v in obj]
    if not isinstance(obj, dict): return obj
    d = {k: project(v) for k, v in obj.items()}
    for k in ('src', 'dst', 'rank'):
        if k in d: d[k] *= 2
    if 'rank_ids' in d: d['rank_ids'] = [r * 2 for r in d['rank_ids']]
    if 'rank_nodes' in d: d['rank_nodes'] = {str(int(k)*2): v for k, v in d['rank_nodes'].items()}
    return d


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'saved-service', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); root = a.repository.resolve(); source = a.saved_service.resolve()
    commit = subprocess.check_output(['git', 'rev-parse', '0477866'], cwd=root, text=True).strip()
    # A later research-only commit must not make the frozen platform unusable.
    subprocess.run(['git', 'diff', '--exit-code', commit, '--', 'python', 'backend', 'deps.lock.json', '.gitmodules'], cwd=root, check=True)
    assert not a.output.exists()
    manifest = load(root/'data/moe-trace-small/manifest.json')
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    raw = {}
    for f in manifest['files']:
        path = root/f['file']['path']
        assert path.stat().st_size < 4*2**20 and sha(path) == f['file']['sha256']
        raw[f['file_id']] = load(path)
    assert len(raw) == 4
    waves = [load(source/f'graphs/g0.w{s}/wave.json') for s in (0, 1)]
    totals = Counter()
    for step, w in enumerate(waves):
        assert w['rank_ids'] == [0, 1, 2, 3] and w['decode_step'] == step
        for route in w['routes']:
            assert len(route['tokens']) == 4
            for t in route['tokens']:
                src = t['source']; experts = raw[src['file_id']][step+1][str(route['layer']+3)][0]
                assert src['source_entry'] == step+1 and src['source_layer_id'] == route['layer']+3
                assert t['experts'] == experts and len(set(experts)) == 8
        for m in w['messages']: totals[m['src'], m['dst']] += m['bytes']
    assert sum(len(w['messages']) for w in waves) == 76 and sum(totals.values()) == 2609152
    assert max(totals.values()) == 444416 < 522289
    a.output.mkdir(); obs = a.output/'observed'; truth = a.output/'truth'; obs.mkdir(); truth.mkdir()
    save(truth/'source.json', dict(manifest=manifest, saved_service=str(source), rank_map={str(r): 2*r for r in range(4)},
        source_files={n: sha(source/n) for n in ('input.json', 'provenance.json', 'frontend.json', 'route_evidence.json')}))
    for name in ('input.json', 'provenance.json', 'frontend.json', 'route_evidence.json'):
        (truth/name).write_bytes((source/name).read_bytes())
    for step, wave in enumerate(waves):
        save(truth/f'g0.w{step}.json', wave); save(obs/f'g0.w{step}.json', project(wave))
    topology = dict(schema_version='moe-scaleout-topology/1',
        deployment=dict(pods=2, segments_per_pod=1, servers_per_segment=2, gpus_per_server=2, gpu_type='NONE'),
        fabric=dict(rails=1, planes=1, tors_per_segment_per_rail=2, upper_tier_scope='shared',
            aggs_per_pod_per_group=2, cores_per_group=2, tor_uplinks=2, agg_uplinks=1, core_wiring='grouped'),
        nic=dict(rails_by_gpu_slot=[[0], [0]], total_rate_bps=100000000000),
        links={k: dict(rate_bps=100000000000 if k == 'access' else 25000000000, delay_ns=1000)
               for k in ('access', 'tor_agg', 'agg_core', 'tor_core')},
        port_limits=dict(gpu=16, tor=128, agg=128, core=128),
        budget=dict(max_nodes=8192, max_links=131072, max_switches=1024))
    save(obs/'topology.json', topology)
    proto = dict(schema_version='moe-scaleout-rdma/2', cc_mode=1, payload_bytes=2000, chunk_bytes=8000,
        ack_interval_bytes=1, base_rtt_ns=14000, switch_buffer_bytes=8388608, seed=11, queue_metrics=True,
        ecn_by_rate={str(r): dict(kmin_bytes=32768, kmax_bytes=131072, pmax=1.0) for r in (100000000000, 25000000000)},
        packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=100000, wire_headers=True),
        mmu_snapshot=dict(nodes=list(range(8, 18)), max_ports=128),
        limits=dict(max_gpus=8, max_switches=10, max_links=32, max_messages=256, max_queue_events=1,
            queue_capture='summary', max_sim_ns=5000000, wall_seconds=60, rss_bytes=2**30, address_bytes=2**31,
            artifact_bytes=96*2**20, result_json_bytes=16*2**20))
    save(obs/'healthy-protocol.json', proto)
    failed = deepcopy(proto)
    failed['link_failures'] = dict(mode='switch_links/1', events=[dict(src=8, dst=10, time_ns=4000, route_update_delay_ns=12000)])
    save(obs/'failed-protocol.json', failed)
    plan = dict(schema_version='moe-rdma-causal-waves/1', waves=['g0.w0.json', 'g0.w1.json'],
        streams=[dict(id=s*4+d, src=s*2, dst=d*2, plane=0, wire_port=21000+s*4+d,
            source_nic=1, destination_nic=1, queue_depth=1024) for s in range(4) for d in range(4) if s != d],
        limits=dict(max_streams=12, max_work=256, max_link_transmissions=100000),
        transport_recovery=dict(mode='go_back_n/1', initial_rto_ns=80000, max_rto_ns=640000, max_retries=3, max_events=30000),
        source_rate_capture=dict(start_ns=0, end_ns=5000000, max_events=30000),
        dcqcn_reference=dict(mode='figure7_reference/1', np_interval_ns=50000, alpha_interval_ns=55000,
            recovery_interval_ns=55000, recovery_bytes=10000000, min_rate_bps=100000000, additive_bps=40000000,
            hyper_bps=50000000, fast_recovery_rounds=5, gain=1/256, cnp_model_bytes=48, max_events=30000))
    save(obs/'gbn-plan.json', plan)
    selective = deepcopy(plan)
    selective['transport_recovery'].update(mode='selective_repeat/1', receive_buffer_bytes=524288, inflight_bytes=522289)
    save(obs/'selective-plan.json', selective)
    cases = [dict(name=f'{n}-{m}', protocol=f'{n}-protocol.json', plan=f'{m}-plan.json')
             for m in ('gbn', 'selective') for n in ('healthy', 'failed')]
    save(obs/'index.json', dict(schema_version='moe-causal-fault/1', platform_commit=commit, cases=cases,
        files={p.name: sha(p) for p in sorted(obs.iterdir())}, messages=76, payload_bytes=2609152,
        original_nodes=216, layers=2, steps=2, physical_active_ranks=[0, 2, 4, 6], window_never_binding=True,
        scope='saved legacy contribution-copy reference, declared compute, no ASTRA replay/modern GIN/calibration/holdout'))
    print(json.dumps(dict(messages=76, payload_bytes=2609152, original_nodes=216, max_stream_bytes=max(totals.values()))))


if __name__ == '__main__': main()
