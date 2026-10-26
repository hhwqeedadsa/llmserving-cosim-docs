"""Independent raw counts, traffic matrix checks and bounded offline replays."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

from experiments.route_coverage.analyze import audit, save
from experiments.route_coverage.catalog import GROUPS, MODEL, plan
from moe_ub_cosim.data_inputs import ROOT


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_real(manifest_path, output):
    manifest = json.loads(manifest_path.read_bytes())
    expected = json.loads((output/'coverage.json').read_bytes())
    traffic = json.loads((output/'traffic-demand.json').read_bytes())
    sources = []
    rows = choices = prompt = steps = 0
    moe = [l for l in manifest['layers'] if l['kind'] == 'moe']
    for f in manifest['files']:
        raw = (ROOT/f['file']['path']).read_bytes()
        assert sha(raw) == f['file']['sha256']
        source = json.loads(raw)
        sources.append(source)
        prompt += len(source[0][str(moe[0]['layer_id'])])
        steps += len(source)-1
        for entry in source:
            for values in entry.values():
                if values is not None:
                    rows += len(values)
                    choices += sum(map(len, values))
    assert (expected['request_files'], expected['prefill_tokens'], expected['decode_token_steps'],
            expected['total_token_layer_rows'], expected['total_expert_choices']) == (len(sources), prompt, steps, rows, choices)
    # Independent counting using Counter over flat expert streams.
    for row in expected['layers']:
        lid = str(row['layer_id'])
        for phase in ('prefill', 'decode'):
            counts = Counter(e for s in sources for ent in (s[:1] if phase == 'prefill' else s[1:])
                             for token in ent[lid] for e in token)
            m = row[phase]
            assert m['selections'] == counts.total()
            assert m['inactive_experts'] == row['num_experts']-len(counts)
            assert m['peak_to_mean'] == max(counts.values())*row['num_experts']/counts.total()
    # Construct a source x destination boolean demand independently; each
    # request has one source token. Then aggregate the declared source groups.
    checked_waves = 0
    for b in traffic['batches']:
        if b['state'] != 'offline_demand_only':
            assert b['batch'] > len(sources)
            continue
        values, loads = [], []
        for group in b['groups']:
            for layer in moe:
                lid = str(layer['layer_id'])
                for step in range(1, min(len(sources[i]) for i in group)):
                    matrix = [[False]*4 for _ in group]
                    for r, index in enumerate(group):
                        experts = sources[index][step][lid][0]
                        for dest in range(4):
                            lo, hi = dest*layer['num_experts']//4, (dest+1)*layer['num_experts']//4
                            matrix[r][dest] = dest != index % 4 and any(lo <= e < hi for e in experts)
                    copies = sum(sum(row) for row in matrix)
                    values.append(copies)
                    if copies:
                        loads.append(max(sum(row[d] for row in matrix) for d in range(4))*4/copies)
        assert len(values) == b['application_waves']
        assert (min(values), max(values)) == (b['remote_vector_copies']['minimum'], b['remote_vector_copies']['maximum'])
        assert sorted(values)[(95*len(values)+99)//100-1] == b['remote_vector_copies']['p95_nearest_rank']
        assert b['dispatch_payload_bytes']['median'] == sorted(values)[(len(values)-1)//2]*manifest['model']['hidden_dim']*2
        assert max(loads) == b['positive_demand_destination_peak_to_mean']['maximum']
        checked_waves += len(values)
    actual_coverage, actual_traffic = audit(manifest_path)
    assert (expected, traffic) == (actual_coverage, actual_traffic)
    return dict(independent_raw_counts=True, independent_histograms=True,
                independent_traffic_waves=checked_waves, deterministic_reanalysis=True)


def check_small(template):
    checks = []
    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        manifest = deepcopy(template)
        manifest['source']['kind'] = 'synthetic_test'
        manifest['source']['usage_terms'] = 'Artificial behavior check, no research distribution evidence.'
        manifest['model'].update(name='artificial', hidden_dim=8, num_layers=1)
        manifest['layers'] = [dict(layer_id=0, kind='moe', num_experts=8, top_k=2)]
        manifest['files'] = []
        for i in range(4):
            source = [{'0': [[i, i+4]]} for _ in range(i+3)]
            raw = json.dumps(source).encode()
            p = root/f'{i}.json'; p.write_bytes(raw)
            manifest['files'].append(dict(file_id=str(i), original_path=f'artificial/{i}.json',
                                         file=dict(path=str(p), sha256=sha(raw)), format='token_layer_map_v1'))
        p = root/'manifest.json'; save(p, manifest)
        report, traffic = audit(p)
        assert (report['total_token_layer_rows'], report['total_expert_choices']) == (18, 36)
        b4 = next(x for x in traffic['batches'] if x['batch'] == 4)
        assert b4['common_decode_steps_per_group'] == [2]
        assert b4['unused_decode_tokens_due_to_common_prefix'] == 6
        checks.append('unequal_lengths_report_unused_tails')
        for mutation, message in (
            (lambda m: m['files'][1]['file'].update(sha256=m['files'][0]['file']['sha256']), 'Duplicate contents'),
            (lambda m: m['files'][0]['file'].update(sha256='0'*64), 'SHA256'),
        ):
            bad = deepcopy(manifest); mutation(bad); save(p, bad)
            try:
                audit(p)
            except ValueError as error:
                assert message in str(error), str(error)
                checks.append(message)
            else:
                raise AssertionError('Corrupt input accepted')
        save(p, manifest)
        try:
            audit(p, max_files=3)
        except ValueError as error:
            assert 'File budget' in str(error)
            checks.append('file_budget')
        else:
            raise AssertionError('Budget exceeded')
    catalogs = {g: dict(complete=False, files=[dict(path=MODEL+'/'+g+f'/{i}.json', size=None, git_blob_id=None)
                                             for i in range(20)]) for g in GROUPS}
    first = plan(catalogs, set())
    for c in catalogs.values():
        c['files'].reverse()
    assert first == plan(catalogs, set())
    assert first['estimated_selected_bytes'] is None and not first['all_catalogs_complete']
    assert not first['data_ready']
    assert Counter(r['split'] for r in first['files']) == {'development': 32, 'holdout_candidate': 32}
    assert len({r['path'] for r in first['files']}) == 64
    checks.extend(['plan_order_independent', 'unknown_identity_not_data_ready', 'request_level_split'])
    return checks


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--analysis-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    report = check_real(a.manifest, a.analysis_dir)
    report.update(passed=True, synthetic_checks=check_small(json.loads(a.manifest.read_bytes())),
                  network_simulations=0, new_route_files=0)
    require_new = not a.output.exists()
    assert require_new, 'Output exists'
    save(a.output, report)
    print(json.dumps(report))


if __name__ == '__main__':
    main()
