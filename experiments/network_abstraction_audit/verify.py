"""Independent arithmetic and structural checks; no network or platform compiler."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(plan):
    plan = deepcopy(plan)
    for op in plan['operations']:
        if op['kind'] == 'expert_count_block':
            op.pop('values')
    return plan


def check_case(original, changed, baseline, plan, row):
    assert {k:v for k,v in original.items() if k != 'calls'} == {
        k:v for k,v in changed.items() if k != 'calls'}
    counts = [0]*256
    per_source = Counter()
    total_tokens = 0
    for old, new in zip(original['calls'], changed['calls'], strict=True):
        assert old['name'] == new['name']
        for src, (olds, news) in enumerate(zip(old['tokens'], new['tokens'], strict=True)):
            for before, after in zip(olds, news, strict=True):
                assert len(after) == len(set(after)) == 8
                assert all(type(x) is int and 0 <= x < 256 for x in after)
                assert [x//64 for x in before] == [x//64 for x in after]
                total_tokens += 1
                for e in after:
                    counts[e] += 1
                    per_source[src,e] += 1
    assert signature(baseline) == signature(plan)
    for op in plan['operations']:
        if op['kind'] == 'expert_count_block':
            # Only one call is in scope, so counts do not mix calls here.
            assert op['call'] == 0
            assert op['values'] == [-per_source[op['src'], e]-1
                for e in range(op['dst']*64, (op['dst']+1)*64)]
    assert row['expert_counts'] == counts
    assert row['hottest_expert_tokens'] == max(counts)
    assert row['active_experts'] == sum(n > 0 for n in counts)
    assert row['hottest_to_mean'] == max(counts)*256/sum(counts)
    assert row['selections'] == 8*total_tokens == sum(counts)
    assert row['tokens'] == total_tokens
    assert row['owner_expert_selection_totals'] == [sum(counts[r*64:(r+1)*64]) for r in range(4)]
    owners = [(src, set(e//64 for e in choices))
        for call in changed['calls'] for src, tokens in enumerate(call['tokens']) for choices in tokens]
    assert row['token_owner_pairs'] == sum(len(ds) for _, ds in owners)
    assert row['remote_token_copies_each_phase'] == sum(len(ds-{src}) for src, ds in owners)
    assert row['data_operand_bytes'] == sum(op['operand_bytes'] for op in plan['operations']
        if op['path']=='rdma' and op['kind']=='token_data')
    assert row['control_operand_bytes'] == sum(op['operand_bytes'] for op in plan['operations']
        if op['path']=='rdma' and op['kind']!='token_data')
    assert row['operations'] == len(plan['operations']) and row['events'] == len(plan['events'])
    assert row['slots'] == len(plan['slots'])
    return sum(counts)


def reject(function):
    try:
        function()
    except AssertionError:
        return
    raise AssertionError('corruption was accepted')


def verify(run, analysis):
    inputs = read(run/'inputs.json')
    for relative, expected in inputs['members'].items():
        assert digest(run/'observed'/relative) == expected
    for relative, expected in inputs['platform_sources'].items():
        assert digest(run/'source'/relative) == expected
    # Confirmation establishes the directory, not the remote content revision.
    assert inputs['raw_directory_confirmed_by_user'] and not inputs['remote_revision_content_verified']
    summary = read(analysis/'summary.json')
    checked = selections = 0
    for batch in (4, 8):
        original = read(run/f'observed/batch{batch}/input.json')['modern']
        baseline = read(run/f'observed/batch{batch}/plan/plan.json')
        assert all(e['kind'] != 'expert_stage_done' or len(e['deps']) == 1 for e in baseline['events'])
        assert read(run/f'observed/batch{batch}/input.json')['costs']['expert_ns'] == 1000
        for variant, row in summary['batches'][str(batch)].items():
            root = analysis/f'batch{batch}'/variant
            changed, plan = read(root/'input.json'), read(root/'plan.json')
            selections += check_case(original, changed, baseline, plan, row)
            checked += 1
    original = read(run/'observed/batch8/input.json')['modern']
    baseline = read(run/'observed/batch8/plan/plan.json')
    changed = read(analysis/'batch8/concentrated/input.json')
    plan = read(analysis/'batch8/concentrated/plan.json')
    row = summary['batches']['8']['concentrated']
    bad = deepcopy(changed)
    bad['calls'][0]['tokens'][0][0][0] += 64
    reject(lambda: check_case(original, bad, baseline, plan, row))
    bad_plan = deepcopy(plan)
    bad_plan['operations'][0]['operand_bytes'] += 1
    reject(lambda: check_case(original, changed, baseline, bad_plan, row))
    bad_plan = deepcopy(plan)
    next(o for o in bad_plan['operations'] if o['kind']=='expert_count_block')['values'][0] -= 1
    reject(lambda: check_case(original, changed, baseline, bad_plan, row))
    bad_row = dict(row, hottest_expert_tokens=row['hottest_expert_tokens']+1)
    reject(lambda: check_case(original, changed, baseline, plan, bad_row))
    assert summary['new_networks'] == 0 and not summary['network_timing_equivalence_executed']
    return dict(passed=True, independent_of_platform_compiler=True,
        input_members_checked=len(inputs['members']), cases=checked,
        selections_counted_including_synthetic_variants=selections,
        corruption_classes=['owner_changed', 'operand_bytes_changed',
            'count_payload_changed', 'hotspot_summary_changed'],
        source_member_hashes_verified=True, only_expert_count_values_excluded=True,
        untimed_schedule_not_network_evidence=True, new_networks=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'analysis', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    a = parser.parse_args()
    result = verify(a.run, a.analysis)
    assert not a.output.exists()
    a.output.write_text(json.dumps(result, indent=2)+'\n')
    print('Independent offline representation checks passed.')
