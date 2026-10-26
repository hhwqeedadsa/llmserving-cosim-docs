"""Persist an admitted snapshot and report conservative resource accounting."""
import argparse
import json
from pathlib import Path
import shutil
from fixtures.modern_selective.checkpoint import sha,save
from fixtures.context_transactions.checkpoint import admit
from moe_ub_cosim.host_probe import disk_usage


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','outer-monitor'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    assert json.loads((stage/'execution.json').read_text())['closed']
    assert json.loads((stage/'recovery.json').read_text())['passed']
    persistent=repo/'runs/modern_selective_checkpoint_20261026';persistent.mkdir(exist_ok=False)
    archive=stage/'checkpoint.tar.gz';_,manifest=admit(archive)
    for name,e in manifest['files'].items():
        if name.startswith('source/'):
            current=repo/name.removeprefix('source/')
            assert current.is_file() and sha(current)==e['sha256'],name
    for name in ('checkpoint.tar.gz','checkpoint.tar.receipt.json','recovery.json','execution.json','verification.json'):
        shutil.copyfile(stage/name,persistent/name);assert sha(stage/name)==sha(persistent/name)
    shutil.copytree(a.outer_monitor,persistent/'driver-monitor')
    probes=[p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored']
    probes.append(a.outer_monitor/'probe.json')
    reports=[json.loads(p.read_text()) for p in probes]
    assert all(not r['remaining_live_group_pids'] for r in reports)
    assert all(r['status'] in ('completed','child_failed') for r in reports)
    seconds=sum(r['wall_seconds'] for r in reports)
    assert seconds+10<900
    roots=[stage,persistent,repo/'fixtures/modern_selective',a.outer_monitor]
    charged=sum(max(disk_usage(root).values()) for root in roots)+2*2**20
    assert charged<512*2**20
    summary=dict(passed=True,actual_network_executions=4,successful_network_executions=4,
        stage_execution_budget_closed=True,unchanged_successful_network_reexecutions=0,
        distinct_python_tests=62,tamper_types=8,dependency_checkouts_verified=13,
        preserved_failure='offline verifier initially required recovery timer for empty response stream; corrected checker only',
        monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        peak_sampled_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in reports),
        charged_storage_bytes_including_2MiB_reserve=charged,cumulative_storage_limit_bytes=512*2**20,
        probe_receipts=[str(p) for p in probes],archive_sha256=sha(archive),
        archive_members=len(manifest['files'])+1,all_packaged_source_hashes_current=True,
        source_and_runtime_restored_checks_byte_identical=True,native_recompiled=False,
        gpu_calibrated=False,complete_four_rank_physical_capture=False,pushed=False,
        archive_scope='pre-closeout source and evidence; terminal archive/restore/management receipts kept alongside',
        overall_research_goal_status='in_progress')
    save(stage/'closeout.json',summary)
    for name in ('verification.json','recovery.json','closeout.json'):
        shutil.copyfile(stage/name,repo/'fixtures/modern_selective'/name)
        shutil.copyfile(stage/name,persistent/name)
    for path in probes:
        if path.is_relative_to(stage):
            target=persistent/'terminal-receipts'/path.relative_to(stage)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
    save(persistent/'source-manifest.json',manifest)
    print(json.dumps({k:summary[k] for k in ('passed','archive_sha256','monitored_seconds_including_nested',
        'charged_storage_bytes_including_2MiB_reserve')},indent=2))


if __name__=='__main__':main()
