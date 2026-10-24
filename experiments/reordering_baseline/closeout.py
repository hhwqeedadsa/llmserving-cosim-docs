"""Persist the verified offline package and bounded resource receipt; no networks."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from experiments.reordering_baseline.checkpoint import admit, sha
from experiments.reordering_baseline.stage import load, save


def usage(paths):
    seen = set(); apparent = allocated = 0
    for root in paths:
        for p in [root, *root.rglob('*')]:
            assert not p.is_symlink(); st = p.stat(); key = st.st_dev, st.st_ino
            if key in seen: continue
            seen.add(key); allocated += st.st_blocks*512
            if p.is_file(): apparent += st.st_size
    return apparent, allocated


def main():
    p = argparse.ArgumentParser()
    for key in ('repository', 'stage'): p.add_argument('--'+key, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); s = a.stage.resolve()
    assert load(s/'run.json')['phase_closed'] and load(s/'restored/replay.json')['passed']
    archive = s/'evidence-final.tar.gz'; payload, manifest = admit(archive)
    assert all(sha(repo/name[len('source/'):]) == entry['sha256']
               for name, entry in manifest['files'].items() if name.startswith('source/'))
    # Require the five actual replay modules and reused research modules to be
    # exactly those already executed from the new source root.
    _, prior = admit(s/'evidence-v1.tar.gz')
    for name in prior['files']:
        if name.startswith('source/') and name.endswith('.py'):
            assert manifest['files'][name] == prior['files'][name]
    monitors = sorted(p for p in s.rglob('probe.json') if 'restored' not in p.relative_to(s).parts)
    reports = [load(p) for p in monitors]
    assert all(r['status'] == 'completed' and r['child_exit_code'] == 0 and not r['remaining_live_group_pids'] for r in reports)
    commands = load(s/'commands.json'); assert all(c['status'] == 'terminal' and c['exit_code'] == 0 for c in commands)
    assert sum(c['network'] for c in commands) == 3
    assert os.statvfs(s).f_bavail*os.statvfs(s).f_frsize > 8*2**30
    assert os.statvfs(repo).f_bavail*os.statvfs(repo).f_frsize > 64*2**20
    runtime = load(s/'runtime.json')
    assert sha(Path(runtime['backend'])) == runtime['sha256']
    assert all(sha(Path(name)) == value for name, value in runtime['libraries'].items())
    assert all(sha(repo/name) == value for name, value in runtime['build_identity']['sources'].items())
    assert all(sha(Path(name)) == value for name, value in runtime['build_identity']['inputs'].items())
    dest = repo/'runs/reordering_baseline_checkpoint_20261024'; assert not dest.exists(); dest.mkdir()
    shutil.copyfile(archive, dest/'evidence.tar.gz')
    shutil.copyfile(archive.with_suffix('.receipt.json'), dest/'archive.json')
    assert sha(dest/'evidence.tar.gz') == sha(archive)
    canonical = repo/'experiments/reordering_baseline/results/20261024'; assert not canonical.exists(); canonical.mkdir(parents=True)
    files = {'summary.json': s/'summary.json', 'verification.json': s/'verification-final.json',
             'source-train-verification.json': s/'source-train-verification.json',
             'identity.json': s/'identity.json', 'archive-safety.json': s/'archive-safety/verification.json',
             'replay.json': s/'restored/replay.json'}
    for name, path in files.items(): shutil.copyfile(path, canonical/name)
    apparent, allocated = usage([s, dest, canonical, repo/'experiments/reordering_baseline'])
    reserve = 2*2**20; charged = max(apparent, allocated)+reserve
    elapsed = sum(r['wall_seconds'] for r in reports)
    assert charged < 384*2**20 and elapsed+10 < 600
    result = dict(schema_version='reordering-baseline-closeout/1', platform_commit=load(s/'runtime.json')['platform_commit'],
        ended_utc=datetime.now(timezone.utc).isoformat(), research_stage_complete=True, overall_paper_goal_complete=False,
        new_actual_networks=3, immutable_saved_baselines=3, successful_network_reruns=0,
        original_terminal_monitors=len(reports), conservative_cumulative_monitored_seconds_including_nested=elapsed,
        final_management_seconds_reserved=10, peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        original_restored_archives_persistent_sources_apparent_bytes=apparent,
        original_restored_archives_persistent_sources_allocated_bytes=allocated,
        source_and_final_receipt_reserve_bytes=reserve, total_charged_with_reserve_bytes=charged,
        limits=dict(cumulative_seconds=600, cumulative_bytes=384*2**20, per_command_seconds=60,
            rss_bytes=2**30, address_bytes=2**31, artifact_bytes=128*2**20,
            max_new_networks=3, max_sim_ns=5000000, result_json_bytes=16*2**20),
        remaining_monitored_live_pids=[], monitored_failures=[],
        unmonitored_diagnostic_read_error=load(s/'diagnostic-notes.json'), archive=load(dest/'archive.json'),
        evidence=dict(final_archive_all_members_verified=True, final_archive_sources_equal_current=True,
            replay_executed_verified_v1=True, five_reports_byte_identical=True,
            final_analysis_sources_same_as_replayed=True, final_archive_network_reexecuted=False,
            runtime_identity=load(s/'identity.json'), fixed_dependencies='13 checkouts agree',
            graph_and_transport_tamper_classes=7, source_train_tamper_classes=3, unsafe_archive_classes=8,
            platform_changes_builds_installs_downloads_gpu_push=False),
        scope='four fixed MMLU requests/two layers/two steps, legacy copy and declared compute; uncalibrated reference, no holdout or algorithm novelty')
    save(canonical/'closeout.json', result); save(dest/'closeout.json', result)
    print('research closed; three new networks, five offline reports identical, no push')


if __name__ == '__main__': main()
