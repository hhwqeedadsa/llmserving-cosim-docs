"""Evaluation only: join fixed candidate windows to separately prepared truth."""
from collections import defaultdict
import json
from pathlib import Path
import sys

from .common import digest, read, require, save
from .observation_spec import GRIDS, grid_id, windows

KEYS = ('sample_id', 'grid', 'start_ns', 'end_ns')
TOTALS = ('declared_rank_ns', 'active_rank_ns', 'selected_rank_ns', 'selected_active_rank_ns',
          'slot_full_wait_rank_ns', 'covered_slot_full_wait_rank_ns', 'other_ready_wait_rank_ns',
          'selected_no_pending_rank_ns', 'target_cells', 'selected_cells', 'false_lead_cells',
          'missed_target_cells', 'cells', 'undetermined_cells')


def accumulate(result, truth, prediction):
    require(type(prediction['selected']) is bool and prediction['state'] == 'undetermined',
            'Candidate is not a validated binary selection with unknown cause')
    width = truth['end_ns']-truth['start_ns']; active = truth['active_ns']
    require(0 <= active <= width and len(truth['ranks']) == 8, 'Invalid truth coverage')
    selected = prediction['selected']
    for blocked, other, no_pending in truth['ranks']:
        require(all(type(v) is int and v >= 0 for v in (blocked, other, no_pending)) and
                blocked+other+no_pending == width and blocked+other <= active, 'Invalid truth partition')
        result['cells'] += 1; result['undetermined_cells'] += 1
        result['declared_rank_ns'] += width; result['active_rank_ns'] += active
        result['slot_full_wait_rank_ns'] += blocked; result['other_ready_wait_rank_ns'] += other
        result['target_cells'] += int(blocked > 0)
        if selected:
            result['selected_cells'] += 1
            result['selected_rank_ns'] += width; result['selected_active_rank_ns'] += active
            result['covered_slot_full_wait_rank_ns'] += blocked
            result['selected_no_pending_rank_ns'] += no_pending
            result['false_lead_cells'] += int(blocked == 0)
        else:
            result['missed_target_cells'] += int(blocked > 0)


def metrics(totals):
    def ratio(a, b): return totals[a]/totals[b] if totals[b] else None
    return dict(**totals,
        investigated_fraction=ratio('selected_rank_ns', 'declared_rank_ns'),
        investigated_active_fraction=ratio('selected_active_rank_ns', 'active_rank_ns'),
        wait_coverage_fraction=ratio('covered_slot_full_wait_rank_ns', 'slot_full_wait_rank_ns'),
        wait_share_of_selected_time=ratio('covered_slot_full_wait_rank_ns', 'selected_rank_ns'),
        false_lead_fraction=ratio('false_lead_cells', 'selected_cells'),
        missed_wait_rank_ns=totals['slot_full_wait_rank_ns']-totals['covered_slot_full_wait_rank_ns'],
        undetermined_fraction=ratio('undetermined_cells', 'cells'))


def evaluate(truth, predictions, output):
    truth, predictions, output = map(Path, (truth, predictions, output))
    manifest = read(truth/'manifest.json'); method = read(predictions/'summary.json')
    require(manifest['schema_version'] == 'scaleup-observation-truth/1' and
            method['schema_version'] == 'scaleup-observation-predictions/1', 'Unsupported evaluation inputs')
    condition = method['condition']
    require(method['observed_manifest_sha256'] == manifest['observed_manifest_sha256'][condition],
            'Prediction and truth refer to different public packages')
    require(digest(truth/'windows.jsonl') == manifest['windows_sha256'] and
            digest(predictions/'predictions.jsonl') == method['predictions_sha256'], 'Evaluation records changed')
    results = []; groups = defaultdict(lambda: dict.fromkeys(TOTALS, 0)); count = 0
    with (truth/'windows.jsonl').open() as ts, (predictions/'predictions.jsonl').open() as ps:
        for binding in manifest['bindings']:
            for period, origin in GRIDS:
                total = dict.fromkeys(TOTALS, 0)
                for a, b in windows(period, origin):
                    t, p = json.loads(ts.readline()), json.loads(ps.readline())
                    expected = (binding['sample_id'], grid_id(period, origin), a, b)
                    require(set(t) == {*KEYS, 'ranks', 'active_ns'} and
                            set(p) == {*KEYS, 'selected', 'state'} and
                            tuple(t[k] for k in KEYS) == tuple(p[k] for k in KEYS) == expected,
                            'Missing, reordered or unexpected evaluation window')
                    accumulate(total, t, p); count += 1
                results.append(dict(sample_id=binding['sample_id'], role=binding['role'],
                    input_set=binding['input_set'], send_slots_per_rank=binding['send_slots_per_rank'],
                    grid=grid_id(period, origin), **metrics(total)))
                group = groups[binding['role'], grid_id(period, origin)]
                for key in TOTALS: group[key] += total[key]
        require(not ts.read() and not ps.read() and count == method['windows'], 'Evaluation coverage mismatch')
    save(output/'evaluation.json', dict(schema_version='scaleup-observation-evaluation/1', condition=condition,
        truth_manifest_sha256=digest(truth/'manifest.json'), predictions_summary_sha256=digest(predictions/'summary.json'),
        cases=results, groups=[dict(role=role, grid=grid, **metrics(total)) for (role, grid), total in groups.items()],
        method_cost=method, qualification='Candidate coverage, not ready/blocked cause classification; no calibrated input'))


if __name__ == '__main__': evaluate(*sys.argv[1:])
