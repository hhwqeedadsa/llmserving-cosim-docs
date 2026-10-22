"""Evaluate fixed placements on future Decode routes; no latency model or tuning."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def dist(hist):
    n = sum(hist.values())
    def at(percent):
        total = 0
        for value, count in sorted(hist.items()):
            total += count
            if total >= (n * percent + 99) // 100: return value
    return dict(count=n, minimum=min(hist), p50=at(50), p95=at(95), maximum=max(hist))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'placements', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args(); resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    assert not a.output.exists() and a.output.parent.is_dir() and shutil.disk_usage(a.output.parent).free >= 8 * 2**30 + 16 * 2**20
    planned = json.loads((a.placements / 'placements.json').read_text())
    assert planned['observed_sha256'] == sha(a.prepared / 'observed/frequencies.json')
    provenance = json.loads((a.prepared / 'truth/provenance.json').read_text())
    assert provenance['decode_sha256'] == sha(a.prepared / 'truth/decode.json')
    routes = json.loads((a.prepared / 'truth/decode.json').read_text())['selections']
    a.output.mkdir(); results = []; baseline = {}
    with (a.output / 'windows.csv').open('w', newline='') as f:
        fields = 'condition layer entry direct_copies remote_expert_contributions incoming_max compute_max matrix_bits compute_by_rank'.split()
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for condition, maps in planned['placements'].items():
            direct = expert = 0; hi = Counter(); hc = Counter(); layers = []
            paired = {name: dict(lower=0, equal=0, higher=0) for name in ('direct_copies', 'incoming_max', 'compute_max')}
            for li in range(58):
                owners = maps[li]; assert [owners.count(r) for r in range(4)] == [64] * 4
                layer_direct = layer_expert = 0; layer_compute = Counter()
                for step in range(128):
                    matrix = [0] * 16; compute = [0] * 4; ne = 0
                    for src in range(4):
                        destinations = set()
                        for e in routes[li][src][step]:
                            dst = owners[e]; compute[dst] += 1; destinations.add(dst); ne += dst != src
                        for dst in destinations:
                            if src != dst: matrix[4 * src + dst] = 1
                    incoming = [sum(matrix[4 * s + d] for s in range(4)) for d in range(4)]
                    copies = sum(matrix); imax = max(incoming); cmax = max(compute)
                    assert sum(compute) == 32
                    current = copies, imax, cmax
                    if condition == 'contiguous': baseline[li, step] = current
                    for name, value, before in zip(paired, current, baseline[li, step]):
                        paired[name]['lower' if value < before else 'higher' if value > before else 'equal'] += 1
                    writer.writerow(dict(condition=condition, layer=li + 3, entry=step + 1,
                        direct_copies=copies, remote_expert_contributions=ne, incoming_max=imax, compute_max=cmax,
                        matrix_bits=''.join(map(str, matrix)), compute_by_rank=':'.join(map(str, compute))))
                    direct += copies; expert += ne; hi[imax] += 1; hc[cmax] += 1
                    layer_direct += copies; layer_expert += ne; layer_compute[cmax] += 1
                layers.append(dict(layer=li + 3, direct_copies=layer_direct,
                    remote_expert_contributions=layer_expert, compute_max=dist(layer_compute)))
            results.append(dict(condition=condition, direct_copies=direct, remote_expert_contributions=expert,
                incoming_max=dist(hi), compute_max=dist(hc), paired_with_contiguous=paired, by_layer=layers))
    (a.output / 'summary.json').write_text(json.dumps(dict(conditions=results, windows_per_condition=7424,
        placements_sha256=sha(a.placements / 'placements.json'), decode_sha256=sha(a.prepared / 'truth/decode.json'),
        window_csv_sha256=sha(a.output / 'windows.csv'), network_executed=False, hardware_calibrated=False,
        latency_predicted=False, optimizer_tuned_on_decode=False, independent_holdout_requests=False),
        ensure_ascii=False, indent=2) + '\n')
    assert sum(p.stat().st_size for p in a.output.iterdir()) < 16 * 2**20
    print(json.dumps([{k: v for k, v in row.items() if k not in ('by_layer', 'paired_with_contiguous')} for row in results]))


if __name__ == '__main__': main()
