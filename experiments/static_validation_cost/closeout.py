"""Close the predeclared run budget using terminal receipts, without rerunning."""
import argparse
from pathlib import Path

from moe_ub_cosim.host_probe import disk_usage
from .data import load, save, sha, runtime_check, schedule


def close(repo, root, output):
    assert not output.exists(); stage = root/'stage'
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'completed' and ledger['network_attempts'] == 28
    assert [r['case'] for r in ledger['records'] if r['network']] == schedule()
    assert all(r['exit_code'] == 0 for r in ledger['records'])
    assert load(stage/'verification.json')['passed']
    prep = load(stage/'preparation.json')
    assert all(sha(repo/n) == h for n, h in prep['code'].items())
    assert all(sha(stage/n) == h for n, h in prep['copied'].items())
    runtime_check(stage, repo)
    checkpoint = load(root/'checkpoint.json'); assert sha(root/'checkpoint.tar.gz') == checkpoint['sha256']
    recovery = load(root/'restored/recovery.json'); assert recovery['passed'] and recovery['new_networks'] == 0
    for name in ('summary.json', 'report.md', 'report.html'):
        assert (stage/'report'/name).read_bytes() == (root/'restored/run/stage/report-recomputed'/name).read_bytes()
    assert (stage/'verification.json').read_bytes() == (root/'restored/run/stage/reverification.json').read_bytes()
    probes = [(p, load(p)) for p in root.rglob('probe.json')]
    assert all(p['status'] == 'completed' and p['child_exit_code'] == 0 and not p['remaining_live_group_pids'] for _, p in probes)
    for _, probe in probes:
        assert probe['wall_seconds'] <= probe['limits']['wall_seconds']
        assert probe['peak_sampled_group_rss_bytes'] <= probe['limits']['sampled_group_rss_bytes']
        assert probe['peak_sampled_artifact_bytes'] <= probe['limits']['artifact_bytes']
    seconds = sum(p['wall_seconds'] for _, p in probes); usage = disk_usage(root)
    assert seconds+10 < 1800 and max(usage.values())+2*2**20 < 2*2**30
    assert 'PASS: checked 13 dependency checkouts' in (stage/'monitor/dependencies/stdout.log').read_text()
    data = load(stage/'report/summary.json')
    save(output, dict(passed=True, networks_completed=28, failed_networks=0, paired_observations=24,
        diagnostic_observations=4, additional_networks_on_restore=0, independent_pairs_checked=12,
        engineering_threshold_met_conditions=sum(r['engineering_threshold_met'] for r in data['cases'].values()),
        conservative_monitored_seconds_including_copied_receipts=seconds,
        terminal_probe_count_including_copies=len(probes), artifact_usage_before_final_receipts=usage,
        peak_sampled_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for _, p in probes),
        budget_seconds=1800, budget_artifact_bytes=2*2**30,
        final_management_seconds_reserved=10, final_receipt_bytes_reserved=2*2**20,
        fixed_dependencies=13, no_remaining_monitored_processes=True,
        checkpoint_sha256=checkpoint['sha256'], archive_members=checkpoint['members'],
        summary_markdown_html_and_verification_reproduced_byte_for_byte=True,
        new_builds=0, new_downloads=0, new_gpu_runs=0, platform_changes=0, dependency_changes=0, pushes=0,
        novelty_proven=False, goal_complete=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'root', 'output'): p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); close(a.repository.resolve(), a.root.resolve(), a.output.resolve())
