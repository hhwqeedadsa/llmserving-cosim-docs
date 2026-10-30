"""Evaluate saved classifier decisions against separately held full histories."""
import argparse
from copy import deepcopy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}

def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--decisions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.prepared/'frozen.json')
    for name, digest in frozen['prepared_sha256'].items():
        assert sha(args.prepared/name) == digest
    root = Path(__file__).resolve().parents[2]
    for name, digest in frozen['source_sha256'].items():
        assert sha(root/name) == digest
    for case in frozen['records'].values():
        assert files(Path(case['output'])) == case['sha256']
    assert sha(Path(__file__).with_name('protocol.md')) == frozen['method_sha256']
    assert sha(Path(__file__).with_name('classify.py')) == frozen['classifier_sha256']
    truth = load(args.prepared/'truth/calibration-and-evaluation.json')
    commands = load(args.decisions/'commands.json')
    assert len(commands) == 18 and all(c['exit_code'] == 0 for c in commands)
    # Decision command arguments contain only a plan, an observed past prefix,
    # a view name and an output path; no truth, result or packet input.
    for command in commands:
        c = command['command']
        assert Path(c[2]).name == 'classify.py' and '--events' in c and '--plan' in c
        assert Path(c[c.index('--events')+1]).parent == (args.prepared/'observed').resolve()
    rows, equivalence, mutations = [], [], []
    spec = importlib.util.spec_from_file_location('checkpoint_judge', Path(__file__).with_name('classify.py'))
    judge_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(judge_module)
    def reject(plan, observed, name):
        try:
            judge_module.judge(plan, observed, 'all_members')
        except ValueError as error:
            mutations.append(dict(name=name, rejected=True, reason=str(error)))
        else:
            raise AssertionError('invalid prefix accepted: '+name)
    for name, path in frozen['plans'].items():
        plan = load(Path(path))
        normal_time = truth[name]['normal_time_ns']
        assert plan['deadline_ns'] == (normal_time//1000+1)*1000
        for condition in ('normal', 'local'):
            raw_path = Path(frozen['records'][condition]['output'])
            raw = events(raw_path/'execution_events.jsonl')
            past = [e for e in raw if e['time_ns'] < plan['deadline_ns']]
            assert past == events(args.prepared/'observed'/f'{name}-{condition}-full.jsonl')
            minimal = [e for e in past if judge_module.relevant(e, plan, 'local_member')]
            assert minimal == events(args.prepared/'observed'/f'{name}-{condition}-minimal.jsonl')
            decisions = {view: load(args.decisions/f'{name}-{condition}-{view}.json')
                         for view in ('all_members', 'local_member', 'final_only')}
            checkpoint_done = truth[name]['conditions'][condition]['actual_complete_ns'] < plan['deadline_ns']
            whole_done = truth[name]['conditions'][condition]['whole_result_ns'] < plan['deadline_ns']
            for view, value in decisions.items():
                expected_done = whole_done if view == 'final_only' else checkpoint_done
                assert value['checkpoint_done'] == expected_done and value['whole_done'] == whole_done
                assert value['consider_backup'] == (not (expected_done or whole_done))
                expected_events = minimal if view == 'local_member' else past
                assert judge_module.judge(plan, expected_events, view) == value
                rows.append(dict(checkpoint=name, condition=condition, view=view,
                    deadline_ns=plan['deadline_ns'], actual_checkpoint_ns=truth[name]['conditions'][condition]['actual_complete_ns'],
                    checkpoint_done=value['checkpoint_done'], whole_done=value['whole_done'],
                    consider_backup=value['consider_backup'], action=value['action'], past_events=value['parsed_past_events']))
            fields = ('checkpoint_done', 'whole_done', 'consider_backup', 'action')
            assert all(decisions['all_members'][k] == decisions['local_member'][k] for k in fields)
            equivalence.append(dict(checkpoint=name, condition=condition, same_decision=True,
                full_past_events=len(past), minimal_past_events=len(minimal),
                scope='one complete milestone flag, not arbitrary local progress; global zero-cost gate model'))
            # Future data is not silently accepted as past input.
            future = deepcopy(past[-1]);future['time_ns'] = plan['deadline_ns'];future['sequence'] += 1
            reject(plan, past+[future], name+'-'+condition+'-same-time')
            future['time_ns'] += 1
            reject(plan, past+[future], name+'-'+condition+'-future')
            reject(plan, past+[deepcopy(past[-1])], name+'-'+condition+'-duplicate')
            reject(plan, past[1:], name+'-'+condition+'-missing-prefix')
            bad = deepcopy(past);bad[-1]['time_ns'] = bad[-2]['time_ns']-1
            reject(plan, bad, name+'-'+condition+'-clock-reversal')
            bad = deepcopy(past);bad[0]['rank'] = 128
            assert bad[0]['kind'].startswith(('node_', 'message_'))
            reject(plan, bad, name+'-'+condition+'-foreign-endpoint')
            bad = deepcopy(past);bad[0]['graph_id'] = 'gINVALID.w0'
            reject(plan, bad, name+'-'+condition+'-bad-graph')
            # Changing any future completion is immaterial to the exact
            # exported prefix; this is not a prediction of changed execution.
            altered = deepcopy(raw)
            for e in altered:
                if e['time_ns'] >= plan['deadline_ns']:
                    e['kind'] = 'unread_future_marker'
                    if 'rank' in e:e['rank'] = 999
            assert [e for e in altered if e['time_ns'] < plan['deadline_ns']] == past
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    with (out/'decisions.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    final_deadline = truth['complete_segment']['deadline_ns']
    summary = dict(passed=True, results=rows, minimal_information_equivalence=equivalence,
        prefix_mutations=mutations, rejected_mutations=len(mutations),
        nominal_detection_advance_ns={name: final_deadline-v['deadline_ns'] for name, v in truth.items()},
        checkpoints=truth, classifier_processes=18, original_runs_unchanged=2,
        new_network_executions=0, new_platform_policy_implemented=False,
        recovery_benefit_measured=False, new_algorithm_benefit_proven=False,
        evaluator_sha256=sha(Path(__file__)),
        constraints='One normal trace and one known local fault, no independent holdout; a past milestone bit is sufficient for these decisions under the global zero-coordination phase model. No p99, detection accuracy distribution, GPU calibration or counterfactual speedup.')
    (out/'analysis.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(dict(passed=True, rejected_mutations=len(mutations), equivalence=equivalence,
        nominal_detection_advance_ns=summary['nominal_detection_advance_ns'], new_network_executions=0)))

if __name__ == '__main__':
    main()
