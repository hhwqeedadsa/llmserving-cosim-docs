"""Independent expert-range oracle and rejection checks for recovery accounting."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import resource
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--account', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert not a.output.exists() and shutil.disk_usage(a.output.parent).free > 8 * 2**30
    manifest_raw = a.manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest['source']['dataset_revision'] == '27febb7b2d24169560a9f6f83d38eb53f94916cf'
    assert manifest['model']['hidden_dim'] == 7168 and len(manifest['files']) == 4
    inputs = json.loads((a.account / 'inputs.json').read_text())
    assert inputs['manifest_sha256'] == hashlib.sha256(manifest_raw).hexdigest()
    records, hashes = [], []
    size = 0
    for item in manifest['files']:
        source = a.repo / item['file']['path']
        size += source.stat().st_size
        assert size <= 16 * 2**20
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == item['file']['sha256']
        records.append(json.loads(raw))
        hashes.append(dict(path=item['file']['path'], sha256=digest))
    assert inputs['files'] == hashes and len({h['sha256'] for h in hashes}) == 4
    assert all(len(r) == 129 for r in records)
    with (a.account / 'cases.csv').open(newline='') as f:
        rows = [{k: (v if k == 'mode' else int(v)) for k, v in row.items()} for row in csv.DictReader(f)]
    expected = {}
    coverage_sets = 0
    for step in range(1, 129):
        for layer in range(3, 61):
            bins = []
            for source in range(4):
                tokens = records[source][step][str(layer)]
                assert len(tokens) == 1
                selected = tokens[0]
                assert len(set(selected)) == len(selected) == 8
                assert all(type(e) is int and e in range(256) for e in selected)
                partitions = [set(selected).intersection(range(64*o, 64*(o+1))) for o in range(4)]
                assert set.union(*partitions) == set(selected)
                assert sum(len(s) for s in partitions) == 8
                bins.append(partitions)
                coverage_sets += 1
            for fail in range(4):
                for mode in ('source_survives', 'colocated_rank_failure'):
                    key = mode, step, layer, fail
                    r = dict(mode=mode, entry=step, layer=layer, failed_owner=fail,
                             valid_tokens=0, lost_source_tokens=0, affected_tokens=0,
                             whole_layer_tasks=0, affected_token_replay_tasks=0,
                             missing_owner_tasks=0, healthy_tasks_on_affected_tokens=0,
                             whole_layer_owner_vectors=0, affected_token_owner_vectors=0,
                             missing_owner_vectors=0, healthy_vectors_on_affected_tokens=0,
                             healthy_expert_vectors_on_affected_tokens=0)
                    for source, partitions in enumerate(bins):
                        if mode == 'colocated_rank_failure' and source == fail:
                            r['lost_source_tokens'] += 1
                            continue
                        r['valid_tokens'] += 1
                        for domain in partitions:
                            r['whole_layer_tasks'] += len(domain)
                            r['whole_layer_owner_vectors'] += bool(domain)
                        if not partitions[fail]: continue
                        r['affected_tokens'] += 1
                        r['missing_owner_vectors'] += 1
                        for owner, domain in enumerate(partitions):
                            r['affected_token_replay_tasks'] += len(domain)
                            r['affected_token_owner_vectors'] += bool(domain)
                            if owner == fail:
                                r['missing_owner_tasks'] += len(domain)
                            else:
                                r['healthy_tasks_on_affected_tokens'] += len(domain)
                                r['healthy_vectors_on_affected_tokens'] += bool(domain)
                                r['healthy_expert_vectors_on_affected_tokens'] += len(domain)
                    expected[key] = r

    def check(candidate):
        seen = set()
        for row in candidate:
            key = row['mode'], row['entry'], row['layer'], row['failed_owner']
            assert key not in seen and row == expected[key], ('case mismatch', key)
            seen.add(key)
        assert seen == set(expected), 'coverage mismatch'

    check(rows)
    summary = json.loads((a.account / 'summary.json').read_text())
    assert summary['cases'] == 59392 and summary['windows'] == 7424
    assert summary['token_layer_units'] == 29696 and summary['hidden_vector_bytes'] == 14336
    for group in summary['groups']:
        subset = [r for key, r in expected.items() if key[0] == group['mode']]
        assert group['cases'] == len(subset) == 29696
        numeric_fields = set(subset[0]) - {'mode', 'entry', 'layer', 'failed_owner'}
        totals = {k: sum(r[k] for r in subset) for k in numeric_fields}
        assert totals == group['totals']
        histogram = Counter(r['affected_tokens'] for r in subset)
        assert group['affected_tokens_histogram'] == {str(k): v for k, v in histogram.items()}
        assert group['work_saved_vs_affected_token_replay_fraction'] == totals['healthy_tasks_on_affected_tokens']/totals['affected_token_replay_tasks']
        for label, field in [('owner', 'healthy_vectors_on_affected_tokens'), ('expert', 'healthy_expert_vectors_on_affected_tokens')]:
            assert group[label+'_output_hidden_bytes_on_affected_tokens'] == totals[field]*14336
            assert group['max_case_'+label+'_output_hidden_bytes'] == max(r[field]*14336 for r in subset)
    assert summary['scope']['actual_savings_lower_bound'] == 0
    assert all(summary['scope'][k] is False for k in ('packet_execution', 'gpu_execution', 'latency_claim',
                                                   'production_failure_sample', 'source_survival_or_timestamps_observed'))
    rejected = []
    tests = [('deleted_case', rows[:-1])]
    changed = dict(rows[0]); changed['healthy_vectors_on_affected_tokens'] += 1
    tests.append(('inflated_retention', [changed] + rows[1:]))
    index = next(i for i, r in enumerate(rows) if r['mode'] == 'colocated_rank_failure')
    changed = dict(rows[index]); changed['valid_tokens'] = 4; changed['lost_source_tokens'] = 0
    tests.append(('lost_source_counted_alive', rows[:index] + [changed] + rows[index+1:]))
    for name, candidate in tests:
        try: check(candidate)
        except AssertionError: rejected.append(name)
        else: raise AssertionError('tamper accepted: '+name)
    result = dict(passed=True, cases_checked=len(rows), raw_token_partitions_checked=coverage_sets,
                  oracle='original expert IDs intersect each of four explicit owner ranges; no account.py import',
                  tamper_rejections=rejected, arithmetic='set coverage only; no numerical expert outputs or floating-point equivalence',
                  sha256={name: hashlib.sha256((a.account/name).read_bytes()).hexdigest()
                          for name in ('cases.csv', 'summary.json', 'inputs.json')})
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
