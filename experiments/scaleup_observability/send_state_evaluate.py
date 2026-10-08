"""Private evaluation of point facts and the windows selected from those facts."""
from collections import defaultdict
import json
from pathlib import Path
import sys

from .common import digest, read, require, save
from .observation_evaluate import KEYS, TOTALS, metrics
from .send_state_spec import STATE_CONDITIONS, STATES, GRIDS, grid_id, windows

EXTRA = ('classified_points','correct_classified_points','correct_held_state_rank_ns',
         'false_no_pending_points','mixed_selected_cells')
CONFUSION = tuple('point_'+actual+'__'+predicted for actual in STATES[:3] for predicted in STATES)
ALL_TOTALS = (*TOTALS, *EXTRA, *CONFUSION)


def accumulate(total, truth, predictions):
    width = truth['end_ns']-truth['start_ns']; active = truth['active_ns']
    require(0 <= active <= width and len(truth['ranks']) == len(truth['point_states']) == len(predictions) == 8,
            'Invalid evaluation coverage')
    for durations, actual, prediction in zip(truth['ranks'],truth['point_states'],predictions):
        require(len(durations) == 3 and all(type(n) is int and n >= 0 for n in durations) and
                sum(durations) == width and sum(durations[:2]) <= active and actual in STATES[:3], 'Invalid truth partition')
        require(set(prediction) == {'selected','state'} and type(prediction['selected']) is bool and
                prediction['state'] in STATES, 'Invalid rank prediction')
        blocked, other, no_pending = durations; selected = prediction['selected']; state = prediction['state']
        total['cells'] += 1; total['undetermined_cells'] += state == 'undetermined'
        total['declared_rank_ns'] += width; total['active_rank_ns'] += active
        total['slot_full_wait_rank_ns'] += blocked; total['other_ready_wait_rank_ns'] += other
        total['target_cells'] += blocked > 0
        if selected:
            total['selected_cells'] += 1; total['selected_rank_ns'] += width; total['selected_active_rank_ns'] += active
            total['covered_slot_full_wait_rank_ns'] += blocked; total['selected_no_pending_rank_ns'] += no_pending
            total['false_lead_cells'] += blocked == 0; total['mixed_selected_cells'] += 0 < blocked < width
        else: total['missed_target_cells'] += blocked > 0
        total['point_'+actual+'__'+state] += 1
        if state != 'undetermined':
            total['classified_points'] += 1; total['correct_classified_points'] += state == actual
            total['correct_held_state_rank_ns'] += durations[STATES.index(state)]
        total['false_no_pending_points'] += state == 'no_pending' and actual != 'no_pending'


def state_metrics(total):
    return dict(**metrics(total),
        point_accuracy_when_classified=total['correct_classified_points']/total['classified_points'] if total['classified_points'] else None,
        held_state_agreement_fraction=total['correct_held_state_rank_ns']/total['declared_rank_ns'])


def evaluate(truth, predictions, output):
    truth, predictions, output = map(Path,(truth,predictions,output))
    manifest = read(truth/'manifest.json'); method = read(predictions/'summary.json')
    require(manifest['schema_version'] == 'scaleup-observation-truth/2' and
            method['schema_version'] in ('scaleup-observation-predictions/1','scaleup-send-state-predictions/1'),
            'Unsupported state evaluation inputs')
    condition = method['condition']; state_method = method['schema_version'] == 'scaleup-send-state-predictions/1'
    require(condition in STATE_CONDITIONS and state_method == (condition == STATE_CONDITIONS[-1]), 'Prediction condition mismatch')
    require(method['observed_manifest_sha256'] == manifest['observed_manifest_sha256'][condition] and
            digest(truth/'windows.jsonl') == manifest['windows_sha256'] and
            digest(predictions/'predictions.jsonl') == method['predictions_sha256'], 'Evaluation input binding/hash mismatch')
    results = []; groups = defaultdict(lambda: dict.fromkeys(ALL_TOTALS,0)); count = 0
    with (truth/'windows.jsonl').open() as ts, (predictions/'predictions.jsonl').open() as ps:
        for binding in manifest['bindings']:
            for period,origin in GRIDS:
                total = dict.fromkeys(ALL_TOTALS,0)
                for a,b in windows(period,origin):
                    t,p = json.loads(ts.readline()),json.loads(ps.readline())
                    expected = (binding['sample_id'],grid_id(period,origin),a,b)
                    require(set(t) == {*KEYS,'ranks','active_ns','point_states'} and
                            set(p) == set(KEYS) | ({'ranks'} if state_method else {'selected','state'}) and
                            tuple(t[k] for k in KEYS) == tuple(p[k] for k in KEYS) == expected,
                            'Missing, reordered or unexpected evaluation window')
                    if state_method:
                        rows = p['ranks']
                        require(all(r['selected'] == (r['state'] == 'full_pending') for r in rows), 'State selection rule differs')
                    else:
                        require(p['state'] == 'undetermined','Port-only baseline must leave endpoint state unknown')
                        rows = [dict(selected=p['selected'],state=p['state']) for _ in range(8)]
                    accumulate(total,t,rows); count += 1
                results.append(dict(sample_id=binding['sample_id'],role=binding['role'],input_set=binding['input_set'],
                    send_slots_per_rank=binding['send_slots_per_rank'],grid=grid_id(period,origin),**state_metrics(total)))
                group = groups[binding['role'],grid_id(period,origin)]
                for key in ALL_TOTALS: group[key] += total[key]
        require(not ts.read() and not ps.read() and count == method['windows'],'Evaluation coverage mismatch')
    save(output/'evaluation.json',dict(schema_version='scaleup-send-state-evaluation/1',condition=condition,
        truth_manifest_sha256=digest(truth/'manifest.json'),predictions_summary_sha256=digest(predictions/'summary.json'),
        cases=results,groups=[dict(role=role,grid=grid,**state_metrics(total)) for (role,grid),total in groups.items()],
        method_cost=method,qualification='Point state is direct telemetry; selecting an entire window is approximate; no downstream root-cause or hardware claim'))


if __name__ == '__main__': evaluate(*sys.argv[1:])
