"""Conditional work/representation bounds from original routes, without timing."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil

REVISION = '27febb7b2d24169560a9f6f83d38eb53f94916cf'
FIELDS = ['mode', 'entry', 'layer', 'failed_owner', 'valid_tokens', 'lost_source_tokens',
          'affected_tokens', 'whole_layer_tasks', 'affected_token_replay_tasks',
          'missing_owner_tasks', 'healthy_tasks_on_affected_tokens',
          'whole_layer_owner_vectors', 'affected_token_owner_vectors',
          'missing_owner_vectors', 'healthy_vectors_on_affected_tokens',
          'healthy_expert_vectors_on_affected_tokens']


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def summarize(rows):
    groups = []
    for mode in ('source_survives', 'colocated_rank_failure'):
        subset = [r for r in rows if r['mode'] == mode]
        totals = {k: sum(r[k] for r in subset) for k in FIELDS[4:]}
        histogram = Counter(r['affected_tokens'] for r in subset)
        groups.append(dict(mode=mode, cases=len(subset), totals=totals,
                           affected_tokens_histogram={str(k): histogram[k] for k in sorted(histogram)},
                           work_saved_vs_affected_token_replay_fraction=
                               totals['healthy_tasks_on_affected_tokens'] / totals['affected_token_replay_tasks'],
                           owner_output_hidden_bytes_on_affected_tokens=totals['healthy_vectors_on_affected_tokens'] * 14336,
                           expert_output_hidden_bytes_on_affected_tokens=totals['healthy_expert_vectors_on_affected_tokens'] * 14336,
                           max_case_owner_output_hidden_bytes=max(r['healthy_vectors_on_affected_tokens'] * 14336 for r in subset),
                           max_case_expert_output_hidden_bytes=max(r['healthy_expert_vectors_on_affected_tokens'] * 14336 for r in subset)))
    return dict(schema='moe-recovery-value/1', windows=7424, token_layer_units=29696,
                cases=len(rows), groups=groups, hidden_vector_bytes=14336,
                scope=dict(deployment='hypothetical EP4, one token/source, expert//64',
                           dtype='BF16 reference, not inferred from AWQ',
                           completion='optimistic: all healthy owner results complete and reusable',
                           alternative_weights='required for completion, not provided by trace',
                           work_unit='one token-expert evaluation; not constant time/FLOPs',
                           totals='sum over counterfactual single-owner failures, not simultaneous memory',
                           actual_savings_lower_bound=0, packet_execution=False, gpu_execution=False,
                           latency_claim=False, production_failure_sample=False,
                           source_survival_or_timestamps_observed=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not a.output.exists()
    assert shutil.disk_usage(a.output.parent).free >= 8 * 2**30 + 64 * 2**20
    manifest_raw = a.manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest['source']['dataset_revision'] == REVISION
    assert len(manifest['files']) == 4 and manifest['model']['hidden_dim'] == 7168
    assert len(manifest['layers']) == 61
    assert all(l['num_experts'] == 256 and l['top_k'] == 8 for l in manifest['layers'][3:])
    records, identities = [], []
    total = 0
    for item in manifest['files']:
        path = a.repo / item['file']['path']
        total += path.stat().st_size
        assert total <= 16 * 2**20
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == item['file']['sha256']
        record = json.loads(raw)
        assert len(record) == 129
        for entry in record[1:]:
            for layer in range(3, 61):
                tokens = entry[str(layer)]
                assert len(tokens) == 1 and len(tokens[0]) == len(set(tokens[0])) == 8
                assert all(type(e) is int and 0 <= e < 256 for e in tokens[0])
        records.append(record)
        identities.append(dict(path=item['file']['path'], sha256=digest))
    assert len({i['sha256'] for i in identities}) == 4
    rows = []
    for entry in range(1, 129):
        for layer in range(3, 61):
            selections = [r[entry][str(layer)][0] for r in records]
            for failed in range(4):
                for mode in ('source_survives', 'colocated_rank_failure'):
                    selections_valid = [s for src, s in enumerate(selections)
                                        if mode == 'source_survives' or src != failed]
                    affected = [s for s in selections_valid if any(e // 64 == failed for e in s)]
                    missing = sum(e // 64 == failed for s in selections_valid for e in s)
                    total_vectors = sum(len({e // 64 for e in s}) for s in selections_valid)
                    affected_vectors = sum(len({e // 64 for e in s}) for s in affected)
                    rows.append(dict(mode=mode, entry=entry, layer=layer, failed_owner=failed,
                                     valid_tokens=len(selections_valid), lost_source_tokens=4-len(selections_valid),
                                     affected_tokens=len(affected), whole_layer_tasks=8*len(selections_valid),
                                     affected_token_replay_tasks=8*len(affected), missing_owner_tasks=missing,
                                     healthy_tasks_on_affected_tokens=8*len(affected)-missing,
                                     whole_layer_owner_vectors=total_vectors,
                                     affected_token_owner_vectors=affected_vectors,
                                     missing_owner_vectors=len(affected),
                                     healthy_vectors_on_affected_tokens=affected_vectors-len(affected),
                                     healthy_expert_vectors_on_affected_tokens=8*len(affected)-missing))
    a.output.mkdir()
    with (a.output / 'cases.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    save(a.output / 'summary.json', summarize(rows))
    save(a.output / 'inputs.json', dict(manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                                       revision=REVISION, files=identities))
    assert sum(p.stat().st_size for p in a.output.iterdir()) < 64 * 2**20
    print(json.dumps(dict(cases=len(rows), summary=str(a.output / 'summary.json'))))


if __name__ == '__main__': main()
