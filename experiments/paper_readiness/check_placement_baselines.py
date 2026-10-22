"""Independently review raw inputs, LPT, window traffic and a pair-statistic counterexample."""
import argparse
from collections import Counter
import csv
import hashlib
from itertools import combinations, product
import json
from pathlib import Path
import resource
import shutil

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_COMMIT = 'b059b85e071c03f14482dfbdd736327f41c3f724'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def dist(values):
    values = sorted(values); n = len(values)
    return dict(count=n, minimum=values[0], p50=values[(n + 1) // 2 - 1],
                p95=values[(n * 95 + 99) // 100 - 1], maximum=values[-1])


def counterexample():
    families = {parity: [[2 * j + bits[j] for j in range(3)] + list(range(6, 11))
                         for bits in product(range(2), repeat=3) if sum(bits) % 2 == parity]
                for parity in (0, 1)}
    marginal = [Counter(e for row in families[p] for e in row) for p in (0, 1)]
    pairs = [Counter(pair for row in families[p] for pair in combinations(sorted(row), 2)) for p in (0, 1)]
    assert marginal[0] == marginal[1] and pairs[0] == pairs[1]
    owners = {}
    for name, remote in (('P', {0, 2, 4}), ('Q', {1, 3, 5})):
        mapping = [None] * 256; capacity = [64] * 4
        for e in range(11):
            rank = 1 if e in remote else 0; mapping[e] = rank; capacity[rank] -= 1
        for e in range(11, 256):
            rank = next(r for r in range(4) if capacity[r]); mapping[e] = rank; capacity[rank] -= 1
        assert capacity == [0] * 4
        owners[name] = mapping
    traffic = {}; compute = {}
    for parity, rows in families.items():
        traffic[str(parity)] = {}; compute[str(parity)] = {}
        for name, mapping in owners.items():
            traffic[str(parity)][name] = sum(any(mapping[e] == rank for e in row)
                                            for row in rows for rank in range(1, 4))
            compute[str(parity)][name] = [sum(mapping[e] == rank for row in rows for e in row) for rank in range(4)]
    # Even parity has three or one of {0,2,4}; odd parity has two or zero.
    # The first review wrote these two labels in reverse; preserve that failure.
    assert traffic == {'0': {'P': 4, 'Q': 3}, '1': {'P': 3, 'Q': 4}}
    assert all(load == [26, 6, 0, 0] for values in compute.values() for load in values.values())
    return dict(scope='Constructed algebraic batch [4,0,0,0], EP4/E256/Top8; no model/network execution',
        selected_families={str(k): v for k, v in families.items()}, placements=owners,
        marginal_counts_identical=True, all_unordered_pair_counts_identical=True,
        expert_pairs_checked=256 * 255 // 2, direct_remote_copies=traffic, compute_inputs=compute,
        new_hypergraph_or_affinity_algorithm_claimed=False)


def sources(source_dir):
    identity = json.loads((source_dir / 'identity.json').read_text())
    commit = json.loads((source_dir / 'commit.json').read_text())
    tree = json.loads((source_dir / 'tree.json').read_text())
    requests = json.loads((source_dir / 'requests.json').read_text())
    assert identity['commit'] == commit['sha'] == REFERENCE_COMMIT
    assert identity['tree'] == commit['commit']['tree']['sha'] == tree['sha'] and not tree['truncated']
    for request in requests['requests']:
        assert request['status'] == 'read' and sha(source_dir / request['target']) == request['sha256']
    files = {row['path']: row for row in requests['files']}; checked = []
    for name, row in files.items():
        blob = next(b for b in tree['tree'] if b['path'] == name)
        content = (source_dir / 'sources' / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == row['sha256'] and len(content) == blob['size']
        assert hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == row['blob'] == blob['sha']
        checked.append(row)
    definitions = [
        ('zhongkai_test/placement_research.py', 'def compute_frequencies(', 'def _detect_model_params('),
        ('zhongkai_test/placement_research.py', 'def generate_greedy_placement(', 'def compute_decode_expert_matrix('),
        ('zhongkai_test/placement_research.py', '    if strategy == "prefill_greedy":', '    if strategy == "real_best_better":'),
        ('zhongkai_test/main_ae.py', '    STRATEGY_DEFS = [', '    STRATEGY_ORDER = '),
    ]
    excerpts = []
    for name, begin, end in definitions:
        path = source_dir / 'sources' / name; text = path.read_text()
        start = text.index(begin); finish = text.index(end, start)
        excerpts.append(dict(path=name, source_sha256=sha(path), first_line=text[:start].count('\n') + 1,
                             last_line=text[:finish].count('\n'), text=text[start:finish]))
    catalogue = json.loads((ROOT / 'docs/review_artifacts/paper_readiness_20261022/sources.json').read_text())
    paper = next(p for p in catalogue['reused_primary_sources'] if p['id'] == 'patterns')
    assert sha(ROOT / paper['local_snapshot']) == paper['sha256']
    assert sha(ROOT / paper['text_snapshot']) == paper['text_sha256']
    lines = (ROOT / paper['text_snapshot']).read_text().splitlines()
    for first, last in ((234, 263), (539, 540), (580, 594), (623, 640)):
        excerpts.append(dict(path=paper['text_snapshot'], source_sha256=paper['text_sha256'],
            first_line=first, last_line=last, text='\n'.join(lines[first - 1:last])))
    return dict(identity=identity, files=checked, excerpts=excerpts, paper=paper,
        requests=len(requests['requests']), response_bytes=sum(r['response_bytes'] for r in requests['requests']),
        request_wall_seconds=sum(r['wall_seconds'] for r in requests['requests']),
        downloaded_code_executed=False, repository_head_is_paper_evaluated_version_proved=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('prepared', 'placements', 'repeat_placements', 'evaluation', 'source_root', 'reference_sources', 'output'):
        p.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    a = p.parse_args(); resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    assert not a.output.exists() and a.output.parent.is_dir() and shutil.disk_usage(a.output.parent).free >= 8 * 2**30 + 16 * 2**20
    manifest = json.loads((a.prepared / 'truth/manifest.json').read_text())
    provenance = json.loads((a.prepared / 'truth/provenance.json').read_text())
    assert provenance['manifest_sha256'] == sha(a.prepared / 'truth/manifest.json')
    raw = []
    for item in manifest['files']:
        path = a.source_root / item['file']['path']; assert sha(path) == item['file']['sha256']
        raw.append(json.loads(path.read_text()))
    observed = json.loads((a.prepared / 'observed/frequencies.json').read_text())
    decode = json.loads((a.prepared / 'truth/decode.json').read_text())['selections']
    for li, layer in enumerate(range(3, 61)):
        for mode in ('all', 'tail10'):
            count = Counter()
            for record in raw:
                rows = record[0][str(layer)]
                if mode == 'tail10': rows = rows[max(0, len(rows) - 10):]
                count.update(e for row in rows for e in row)
            assert [count[e] for e in range(256)] == observed['counts'][mode][li]
        for src, record in enumerate(raw):
            assert decode[li][src] == [entry[str(layer)][0] for entry in record[1:]]
    path = a.placements / 'placements.json'
    assert path.read_bytes() == (a.repeat_placements / 'placements.json').read_bytes()
    planned = json.loads(path.read_text())
    assert planned['observed_sha256'] == provenance['observed_sha256'] == sha(a.prepared / 'observed/frequencies.json')
    assert provenance['decode_sha256'] == sha(a.prepared / 'truth/decode.json')
    assert list(planned['placements']) == ['contiguous', 'prefill_all_lpt', 'prefill_tail10_lpt']
    for condition, maps in planned['placements'].items():
        for li, mapping in enumerate(maps):
            assert len(mapping) == 256 and [mapping.count(r) for r in range(4)] == [64] * 4
            expected = [e // 64 for e in range(256)]
            if condition != 'contiguous':
                counts = observed['counts']['all' if condition == 'prefill_all_lpt' else 'tail10'][li]
                loads = [0] * 4; remaining = [64] * 4; expected = [None] * 256
                for e in sorted(range(256), key=lambda e: (-counts[e], e)):
                    r = min((r for r in range(4) if remaining[r]), key=lambda r: (loads[r], r))
                    expected[e] = r; loads[r] += counts[e]; remaining[r] -= 1
            assert expected == mapping
    summary = json.loads((a.evaluation / 'summary.json').read_text())
    assert summary['placements_sha256'] == sha(path) and summary['decode_sha256'] == provenance['decode_sha256']
    assert summary['window_csv_sha256'] == sha(a.evaluation / 'windows.csv')
    matrix_entries = rows_checked = 0; results = []; baseline = {}
    with (a.evaluation / 'windows.csv').open(newline='') as f:
        reader = csv.DictReader(f)
        for condition, maps in planned['placements'].items():
            total_copies = total_expert = 0; maxima_in = []; maxima_compute = []; layers = []
            paired = {name: dict(lower=0, equal=0, higher=0) for name in ('direct_copies', 'incoming_max', 'compute_max')}
            for li, mapping in enumerate(maps):
                layer_copies = layer_expert = 0; layer_max = []
                for step in range(128):
                    selections = [raw[src][step + 1][str(li + 3)][0] for src in range(4)]
                    matrix = [int(src != dst and any(mapping[e] == dst for e in selections[src]))
                              for src in range(4) for dst in range(4)]
                    compute = [sum(mapping[e] == dst for selected in selections for e in selected) for dst in range(4)]
                    incoming = [sum(matrix[d::4]) for d in range(4)]
                    copies, imax, cmax = sum(matrix), max(incoming), max(compute)
                    ne = sum(mapping[e] != src for src in range(4) for e in selections[src])
                    row = next(reader); rows_checked += 1; matrix_entries += 16
                    assert (row['condition'], int(row['layer']), int(row['entry'])) == (condition, li + 3, step + 1)
                    assert (int(row['direct_copies']), int(row['incoming_max']), int(row['compute_max']), int(row['remote_expert_contributions'])) == (copies, imax, cmax, ne)
                    assert row['matrix_bits'] == ''.join(map(str, matrix)) and row['compute_by_rank'] == ':'.join(map(str, compute))
                    current = copies, imax, cmax
                    if condition == 'contiguous': baseline[li, step] = current
                    for name, v, b in zip(paired, current, baseline[li, step]):
                        paired[name]['lower' if v < b else 'higher' if v > b else 'equal'] += 1
                    total_copies += copies; total_expert += ne; maxima_in.append(imax); maxima_compute.append(cmax)
                    layer_copies += copies; layer_expert += ne; layer_max.append(cmax)
                layers.append(dict(layer=li + 3, direct_copies=layer_copies, remote_expert_contributions=layer_expert, compute_max=dist(layer_max)))
            results.append(dict(condition=condition, direct_copies=total_copies, remote_expert_contributions=total_expert,
                incoming_max=dist(maxima_in), compute_max=dist(maxima_compute), paired_with_contiguous=paired, by_layer=layers))
        assert next(reader, None) is None
    assert results == summary['conditions']
    source_audit = sources(a.reference_sources)
    proof = counterexample()
    a.output.mkdir()
    for name, value in [('sources.json', source_audit), ('pair_counterexample.json', proof),
                         ('verification.json', dict(passed=True, source_files_checked=4,
                          prefill_count_cells_checked=58 * 256 * 2, decode_token_layer_rows_checked=29696,
                          balanced_maps_checked=58 * 3, condition_window_rows_checked=rows_checked,
                          destination_matrix_entries_checked=matrix_entries, placements_relocated_byte_identical=True,
                          summary_sha256=sha(a.evaluation / 'summary.json'), csv_sha256=sha(a.evaluation / 'windows.csv'),
                          independent_linear_lpt_matches_heap=True, pair_counterexample_verified=True,
                          network_executed=False, hardware_calibrated=False, goal_complete=False))]:
        (a.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(passed=True, checked_windows=rows_checked, checked_matrix_entries=matrix_entries,
        source_files=len(source_audit['files']), pairwise_identical_volume_ranking_reverses=True)))


if __name__ == '__main__': main()
