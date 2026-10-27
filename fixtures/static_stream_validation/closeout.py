"""Read terminal receipts, preserve checker failures and close the fixed budget."""
import argparse
from pathlib import Path
import re

from fixtures.static_stream_validation.verify import load, save, sha
from fixtures.static_stream_validation.accept import LABELS, identity_check, library_check, source_check
from moe_ub_cosim.host_probe import disk_usage


def close(stage, output):
    assert not output.exists()
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'completed' and ledger['network_attempts'] == 6
    attempts = [r for r in ledger['records'] if r['network']]
    assert [r['label'] for r in attempts] == list(LABELS) and all(r['exit_code'] == 0 for r in attempts)
    assert load(stage/'verification.json')['passed']
    assert (stage/'verification.json').read_bytes() == (stage/'restored/reverification.json').read_bytes()
    assert load(stage/'restored/recovery.json')['passed']
    checkpoint = load(stage/'checkpoint.json')
    assert sha(stage/'checkpoint.tar.gz') == checkpoint['sha256']
    identity = load(stage/'runtime-identity.json')
    for source in ('source', 'source-initial', 'source-checker-v1'):
        source_check(stage/source)
    for name in ('runtime', 'migrated-runtime'):
        identity_check(stage/'source', stage/name, identity)
    for label in LABELS:
        library_check(stage/label, stage/('migrated-runtime' if label.endswith('-migrated') else 'runtime'), identity)
    # Include copied historical receipts conservatively, not as new work.
    probes = [(p, load(p)) for p in stage.rglob('probe.json')]
    assert all(not p['remaining_live_group_pids'] for _, p in probes)
    allowed_failures = {str(stage/'monitor/driver/probe.json'), str(stage/'monitor/continued-driver/probe.json'),
                        str(stage/'monitor/transaction-full-verify/probe.json')}
    failures = []
    for path, p in probes:
        assert p['peak_sampled_group_rss_bytes'] <= p['limits']['sampled_group_rss_bytes']
        assert p['wall_seconds'] <= p['limits']['wall_seconds']
        assert p['peak_sampled_artifact_bytes'] <= p['limits']['artifact_bytes']
        if p['status'] != 'completed':
            # The archive contains copies of these exact failure receipts.
            assert any(str(path).endswith(str(Path(n).relative_to(stage))) for n in allowed_failures)
            assert p['status'] == 'child_failed' and p['child_exit_code'] == 1
            failures.append(str(path.relative_to(stage)))
    seconds = sum(p['wall_seconds'] for _, p in probes)
    size = disk_usage(stage)
    assert seconds+10 < 900 and max(size.values())+2*2**20 < 512*2**20
    unit = []
    for directory in (stage/'monitor').iterdir():
        if directory.name == 'unit-initial' or directory.name.startswith('unit-'):
            text = (directory/'stderr.log').read_text()
            match = re.search(r'Ran (\d+) tests?', text)
            assert match and '\nOK\n' in text
            unit.append(dict(suite=directory.name, tests=int(match[1])))
    assert sum(r['tests'] for r in unit) == 81
    dependency = (stage/'monitor/dependencies/stdout.log').read_text()
    assert '13' in dependency
    report = dict(passed=True, networks_completed=6, networks_failed=0, new_networks_on_restore=0,
        no_successful_network_repeated=True, unit_suites=unit, unit_tests=81, fixed_dependencies=13,
        conservative_monitored_seconds_including_copied_receipts=seconds,
        terminal_probe_count_including_copies=len(probes),
        peak_sampled_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for _, p in probes),
        artifact_usage_before_final_receipts=size, budget_seconds=900, budget_artifact_bytes=512*2**20,
        remaining_final_management_seconds_reserved=10, remaining_receipt_bytes_reserved=2*2**20,
        preserved_checker_failure_probes=failures, archived_source_snapshots=3,
        checkpoint_sha256=checkpoint['sha256'], actual_runtime_files_checked=10,
        original_and_migrated_library_hashes_checked=True, no_remaining_monitored_processes=True,
        performance_measured=False, hardware_calibrated=False, research_goal_complete=False,
        new_builds=0, new_downloads=0, new_gpu_runs=0, dependency_changes=0, pushes=0)
    save(output, report)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); close(a.stage.resolve(), a.output.resolve())
