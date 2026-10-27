"""Close the declared platform stage without another build or simulation."""
import argparse
import json
from pathlib import Path

from moe_ub_cosim.host_probe import disk_usage


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve()
    verification = load(stage/'verification.json')
    recovery = load(stage/'recovered/recovery.json')
    assert verification['passed'] and recovery['passed']
    monitors = []
    for path in sorted(stage.rglob('probe.json')):
        if path.is_relative_to(stage/'recovered'):
            continue
        row = load(path)
        monitors.append(dict(path=str(path.relative_to(stage)), status=row['status'],
                             seconds=row['wall_seconds'], rss=row['peak_sampled_group_rss_bytes']))
    expected_failures = {'regression/probe.json', 'network-monitor/probe.json'}
    assert {row['path'] for row in monitors if row['status'] != 'completed'} == expected_failures
    assert load(stage/'build/logs/configure/probe.json')['wall_seconds'] < 60
    assert load(stage/'build/logs/compile/probe.json')['wall_seconds'] < 300
    assert load(stage/'regression-corrected/probe.json')['child_exit_code'] == 0
    assert load(stage/'affected-regression/probe.json')['child_exit_code'] == 0
    elapsed = sum(row['seconds'] for row in monitors)
    usage = disk_usage(stage)
    assert elapsed + 10 < 900 and max(usage.values()) + 2*2**20 < 2**30
    management = (stage/'management/stdout.log').read_text()
    assert 'PASS: checked 13 dependency checkouts' in management
    receipt = load(stage/'checkpoint.tar.receipt.json')
    result = dict(passed=True, original_terminal_monitors=monitors,
        conservative_monitored_seconds_including_nested=elapsed, wall_budget_seconds=900,
        closing_receipt_seconds_reserved=10, artifact_usage=usage, artifact_budget_bytes=2**30,
        closing_receipt_bytes_reserved=2*2**20,
        peak_sampled_group_rss_bytes=max(row['rss'] for row in monitors),
        new_identity_test_methods=11, scaleout_regression_cases=153, focused_regression_cases=49,
        regression_counts_are_executions_not_claimed_distinct_methods=True,
        fresh_configures=1, fresh_full_builds=1, network_executions=1,
        copied_source_recovery=True, additional_recovery_builds=0, additional_recovery_networks=0,
        preserved_failures=dict(regression='removed shared constant still imported by three modules; corrected before acceptance',
                                network_command='argument parser rejected --protocol; no backend launched; --config corrected'),
        dependency_checkouts_verified=13, dependencies_changed=False, gpu_executed=False,
        downloaded=False, pushed=False, same_plane_multi_nic_implemented=False,
        overall_research_goal_completed=False, archive=receipt)
    save(stage/'closeout.json', result)
    print(json.dumps({key: value for key, value in result.items() if key != 'original_terminal_monitors'}))


if __name__ == '__main__': main()
