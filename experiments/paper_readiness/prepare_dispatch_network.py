"""Prepare an explicitly data-only causal microbenchmark from fixed real routes."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest', 'placements', 'config', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--source-root', type=Path, default=ROOT)
    a = p.parse_args(); out = a.output.resolve()
    assert not out.exists() and out.parent.is_dir() and shutil.disk_usage(out.parent).free >= 8 * 2**30 + 16 * 2**20
    cfg = json.loads(a.config.read_text()); assert sha(a.placements) == cfg['placements_sha256']
    assert cfg['source_layer'] == 3 and cfg['decode_entries'] == [1, 2, 3, 4]
    assert cfg['logical_to_gpu'] == [0, 2, 4, 6] and cfg['token_bytes'] == 14464
    assert list(cfg['network_profiles']) == ['all100g', 'upper25g']
    placements = json.loads(a.placements.read_text())['placements']
    assert list(placements) == cfg['placement_conditions'] == ['contiguous', 'prefill_all_lpt', 'prefill_tail10_lpt']
    manifest = json.loads(a.manifest.read_text()); assert len(manifest['files']) == 4
    selected = []; total = 0
    for row in manifest['files']:
        path = a.source_root.resolve() / row['file']['path']; total += path.stat().st_size
        assert total < 16 * 2**20 and sha(path) == row['file']['sha256']
        raw = json.loads(path.read_text())
        rows = [raw[entry]['3'][0] for entry in cfg['decode_entries']]
        assert all(len(row) == len(set(row)) == 8 and all(type(e) is int and 0 <= e < 256 for e in row) for row in rows)
        selected.append(rows)
    out.mkdir(); observed = out / 'observed'; truth = out / 'truth'; observed.mkdir(); truth.mkdir()
    topology = json.loads((ROOT / 'examples/scaleout_rdma/small-clos-100g.json').read_text())
    topology['deployment']['gpu_type'] = 'NONE'
    protocol = json.loads((ROOT / 'examples/scaleout_rdma/rdma.json').read_text())
    protocol.update(schema_version='moe-scaleout-rdma/2', queue_metrics=True,
        packet_capture=dict(nodes=[], start_ns=0, end_ns=5000000, max_events=100000),
        limits=dict(max_gpus=32, max_switches=16, max_links=256, max_messages=8192,
            max_queue_events=200000, queue_capture='summary', max_sim_ns=5000000,
            wall_seconds=60, rss_bytes=2**30, address_bytes=2 * 2**30, artifact_bytes=64 * 2**20, result_json_bytes=16 * 2**20))
    protocol['ecn_by_rate']['25000000000'] = deepcopy(protocol['ecn_by_rate']['100000000000'])
    save(observed / 'protocol.json', protocol)
    for name, rate in cfg['network_profiles'].items():
        physical = deepcopy(topology)
        for tier in ('tor_agg', 'agg_core', 'tor_core'): physical['links'][tier]['rate_bps'] = rate
        save(observed / ('topology-' + name + '.json'), physical)
    streams = [dict(id=4 * src + dst, src=cfg['logical_to_gpu'][src], dst=cfg['logical_to_gpu'][dst], plane=0,
        wire_port=cfg['wire_port_base'] + 4 * src + dst, source_nic=1, destination_nic=1, queue_depth=cfg['queue_depth'])
        for src in range(4) for dst in range(4) if src != dst]
    work_truth = {}
    for name in cfg['placement_conditions']:
        owners = placements[name][0]; assert [owners.count(r) for r in range(4)] == [64] * 4
        work = []; previous = []; details = []
        for step, entry in enumerate(cfg['decode_entries']):
            current = []; compute = [0] * 4
            for src in range(4):
                choices = selected[src][step]
                for e in choices: compute[owners[e]] += 1
                for dst in sorted({owners[e] for e in choices} - {src}):
                    ident = step * 16 + 4 * src + dst; current.append(ident)
                    work.append(dict(id=ident, stream=4 * src + dst, bytes=cfg['token_bytes'],
                        after=[dict(work=parent, event='acked') for parent in previous]))
                    details.append(dict(work=ident, entry=entry, source_rank=src, destination_rank=dst))
            assert current and sum(compute) == 32
            previous = current
        assert len(work) <= 64
        save(observed / ('work-' + name + '.json'), dict(schema_version='moe-rdma-stream-work/1', streams=streams, work=work))
        work_truth[name] = details
    save(observed / 'index.json', dict(config=cfg, artifacts={p.name: dict(sha256=sha(p), bytes=p.stat().st_size)
        for p in sorted(observed.iterdir())}, scope='Declared ACK-round data-only native network microbenchmark; not modern DeepEP inference'))
    shutil.copy2(a.manifest, truth / 'manifest.json'); shutil.copy2(a.placements, truth / 'placements.json')
    save(truth / 'provenance.json', dict(manifest_sha256=sha(a.manifest), placements_sha256=sha(a.placements),
        selections=selected, work_source_mapping=work_truth, input_bytes=total, logical_to_gpu=cfg['logical_to_gpu'],
        raw_launch_times_observed=False, latency_calibrated=False, full_modern_library_modeled=False))
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 16 * 2**20
    print(json.dumps(dict(prepared=True, network_conditions=6, data_only=True)))


if __name__ == '__main__': main()
