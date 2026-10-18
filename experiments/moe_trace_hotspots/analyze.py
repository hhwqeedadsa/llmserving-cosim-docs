"""Bounded, source-checked expert statistics; no network execution or downloads.

Uses the repository's token_layer_map_v1 validator. Windows are per-request
Decode token windows, not wall-clock time or serving batches.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path

from moe_ub_cosim.route_evidence import (iter_sources, unique_object,
                                       validate_manifest)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def top(counts, limit):
    return sorted((e for e, n in enumerate(counts) if n),
                  key=lambda e: (-counts[e], e))[:limit]


def jaccard(left, right):
    left, right = set(left), set(right)
    return len(left & right) / len(left | right) if left or right else None


def describe(counts, tokens, top_m):
    total = sum(counts)
    mean = total / len(counts)
    winners = top(counts, top_m)
    return dict(tokens=tokens, selections=total, counts=counts,
                activation_probability=[n / tokens if tokens else None for n in counts],
                selection_share=[n / total if total else None for n in counts],
                max_to_mean=max(counts) / mean if mean else None,
                coefficient_of_variation=(math.sqrt(sum((n-mean)**2 for n in counts)
                                                   / len(counts)) / mean) if mean else None,
                inactive_experts=sum(n == 0 for n in counts), top_experts=winners,
                top_expert_selection_share=sum(counts[e] for e in winners) / total if total else None)


def analyze(manifest_path, file_ids=None, *, window=16, top_m=8,
            max_files=16, max_total_bytes=67108864):
    require(1 <= window <= 4096 and 1 <= top_m <= 256, 'Invalid window/top-m')
    require(1 <= max_files <= 4096 and 1 <= max_total_bytes <= 1073741824,
            'Invalid explicit input budget')
    path = Path(manifest_path)
    require(path.stat().st_size <= 2097152, 'Manifest exceeds 2 MiB')
    raw_manifest = path.read_bytes()
    manifest = json.loads(raw_manifest, object_pairs_hook=unique_object)
    validate_manifest(manifest)
    selected = deepcopy(manifest)
    if file_ids:
        require(len(file_ids) == len(set(file_ids)), 'Duplicate selected file ID')
        selected['files'] = [f for f in manifest['files'] if f['file_id'] in file_ids]
        require({f['file_id'] for f in selected['files']} == set(file_ids), 'Unknown file ID')
    require(0 < len(selected['files']) <= max_files,
            'Too many files: select --file-id explicitly or declare a larger budget')
    sizes = []
    for f in selected['files']:
        size = Path(f['file']['path']).stat().st_size
        require(size <= 16777216, 'Raw file exceeds 16 MiB')
        sizes.append(size)
    require(sum(sizes) <= max_total_bytes, 'Total raw bytes exceed declared budget')
    layers = [l for l in manifest['layers'] if l['kind'] == 'moe']
    require(len(layers) <= 128 and all(l['num_experts'] <= 1024 for l in layers),
            'Layer/expert dimensions exceed analysis support')
    # Co-activation is quadratic in Top-K; this bounds actual traversal work.
    require(all(l['top_k'] <= 16 for l in layers), 'Top-K exceeds analysis budget')
    aggregate = {l['layer_id']: {phase: dict(counts=[0]*l['num_experts'], tokens=0)
                                for phase in ('prefill', 'decode')} for l in layers}
    requests = []
    pair_work = report_cells = 0
    # iter_sources checks SHA-256, duplicate JSON keys, every layer/entry and Top-K.
    for file_id, source in iter_sources(selected):
        rows = source['raw']
        tokens = source['prompt_tokens'] + source['available_decode_records']
        pair_work += tokens * sum(l['top_k']*(l['top_k']-1)//2 for l in layers)
        report_cells += sum(8*l['num_experts'] +
                            ((len(rows)-2)//window+1)*(top_m+8) for l in layers)
        require(pair_work <= 16000000 and report_cells <= 2000000,
                'Analysis work/report budget exceeded; explicitly select fewer files')
        request = dict(file_id=file_id, original_path=source['original_path'],
                       prompt_tokens=source['prompt_tokens'],
                       decode_records=source['available_decode_records'], layers=[])
        for layer in layers:
            lid, experts = layer['layer_id'], layer['num_experts']
            phases = {}; pair_rows = {}
            for phase, entries in (('prefill', rows[:1]), ('decode', rows[1:])):
                counts = [0]*experts; tokens = 0; pairs = Counter()
                for entry in entries:
                    for choices in entry[str(lid)]:
                        tokens += 1
                        for e in choices:
                            counts[e] += 1
                        pairs.update(combinations(sorted(choices), 2))
                require(sum(counts) == tokens*layer['top_k'], 'Selection conservation failed')
                phases[phase] = describe(counts, tokens, top_m)
                pair_rows[phase] = [dict(expert_ids=list(pair), token_count=n)
                                    for pair, n in sorted(pairs.items(), key=lambda x: (-x[1], x[0]))[:top_m]]
                merged = aggregate[lid][phase]
                merged['tokens'] += tokens
                merged['counts'] = [a+b for a, b in zip(merged['counts'], counts)]
            windows = []
            for start in range(1, len(rows), window):
                counts = [0]*experts
                for entry in rows[start:start+window]:
                    for e in entry[str(lid)][0]:
                        counts[e] += 1
                winners = top(counts, top_m)
                windows.append(dict(first_source_entry=start, tokens=min(window, len(rows)-start),
                                    top_experts=winners, max_to_mean=max(counts)/(sum(counts)/experts),
                                    previous_top_jaccard=jaccard(windows[-1]['top_experts'], winners)
                                    if windows else None))
            request['layers'].append(dict(layer_id=lid, phases=phases, top_coactivated_pairs=pair_rows,
                prefill_decode_top_jaccard=jaccard(phases['prefill']['top_experts'], phases['decode']['top_experts']),
                decode_token_windows=windows))
        requests.append(request)
    return dict(schema_version='moe-trace-hotspots/1', source=manifest['source'], model=manifest['model'],
        real_trace=manifest['source']['kind'] != 'synthetic_test',
        manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
        selected_files=selected['files'], input_bytes=sum(sizes), top_m=top_m, token_window_size=window,
        analyzed_pair_events=pair_work, estimated_report_cells=report_cells,
        source_subset='explicit_manifest_files_no_population_inference',
        network_executed=False, windows_are_physical_time=False,
        eos_inferred=False, top_tie_break='positive_count_descending_then_expert_id_ascending',
        coactivation_scope='raw_counts_no_control_for_marginal_popularity',
        aggregate_weighting='token_weighted_with_prefill_and_decode_separate',
        aggregate=[dict(layer_id=l['layer_id'], num_experts=l['num_experts'], top_k=l['top_k'],
                        phases={p: describe(v['counts'], v['tokens'], top_m)
                                for p, v in aggregate[l['layer_id']].items()}) for l in layers],
        requests=requests)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--file-id', action='append', dest='file_ids')
    p.add_argument('--window', type=int, default=16)
    p.add_argument('--top-m', type=int, default=8)
    p.add_argument('--max-files', type=int, default=16)
    p.add_argument('--max-total-bytes', type=int, default=67108864)
    p.add_argument('--output', type=Path, required=True, help='New output JSON file')
    a = p.parse_args()
    require(not a.output.exists(), 'Output already exists')
    report = analyze(a.manifest, a.file_ids, window=a.window, top_m=a.top_m,
                     max_files=a.max_files, max_total_bytes=a.max_total_bytes)
    encoded = json.dumps(report, indent=2)+'\n'
    require(len(encoded.encode()) <= 67108864, 'Output exceeds 64 MiB')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x') as stream:
        stream.write(encoded)
    print(json.dumps(dict(files=len(report['requests']), real_trace=report['real_trace'],
                          network_executed=False, output=str(a.output))))


if __name__ == '__main__':
    main()
