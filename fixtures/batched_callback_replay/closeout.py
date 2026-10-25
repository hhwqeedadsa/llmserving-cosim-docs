"""Seal offline scope, identities, terminal jobs and the registered resource caps."""
import argparse
from pathlib import Path
from fixtures.context_transactions.checkpoint import admit
from fixtures.batched_callback_replay.stage import load,save,sha,usage,roots
from moe_ub_cosim.host_probe import disk_usage


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','archive'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve();archive=a.archive.resolve()
    journal=load(stage/'commands.json')
    assert all(row['status']=='terminal' and row['exit_code']==0 and row['network'] is False for row in journal)
    assert sum(row['label']=='saved-record-audit' for row in journal)==1
    assert sum(row['label']=='offline-recovery' for row in journal)==1
    probes=sorted(path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored')
    reports=[load(path) for path in probes]
    assert all((stage/(row['label']+'-monitor/probe.json')).is_file() for row in journal)
    assert all(report['status']=='completed' and not report['remaining_live_group_pids'] for report in reports)
    groups={report['process_group'] for report in reports}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:fields=path.read_text().rsplit(')',1)[1].split()
        except (OSError,IndexError):continue
        assert fields[0]=='Z' or int(fields[2]) not in groups
    identity=load(stage/'identity.json');native=identity['native_runtime'];previous=Path(identity['source_stage'])
    assert all(sha(repo/name)==digest for name,digest in identity['platform_sources'].items())
    assert all(sha(repo/name)==digest for name,digest in native['sources'].items())
    assert all(sha(previous/'runtime'/name)==digest for name,digest in native['files'].items())
    assert all(sha(stage/name)==digest for name,digest in load(stage/'inputs.json')['files'].items())
    copied=stage/'restored/source'
    assert all(sha(repo/name)==sha(copied/name) for name in identity['platform_sources'])
    summary=load(stage/'summary.json');assert summary['verified'] and summary['offline_recovery_complete']
    assert summary['physical_interface_implemented'] is False and summary['representative_four_rank_call_complete'] is False
    assert load(stage/'recovery.json')['passed']
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    members,manifest=admit(archive)
    assert all(sha(repo/name[7:])==row['sha256'] for name,row in manifest['files'].items() if name.startswith('source/'))
    assert sha(archive)==load(archive.with_suffix('.receipt.json'))['sha256']
    seconds=sum(report['wall_seconds'] for report in reports);charged=usage(roots(repo,stage))+2**20
    seen=set();sizes=dict(apparent_bytes=0,allocated_bytes=0)
    for root in (stage,archive.parent):
        for key,value in disk_usage(root,seen).items():sizes[key]+=value
    artifact=max(sizes.values())
    assert seconds+10<240 and charged<96*2**20 and artifact+2**20<48*2**20
    assert all(report['wall_seconds']<60 and report['peak_sampled_group_rss_bytes']<2**30 for report in reports)
    receipt=dict(verified=True,offline_stage_status='completed',overall_goal_status='in_progress',
        stage_execution_budget_closed=True,network_executions=0,builds=0,gpu_executed=False,
        original_saved_record_audit_executions=1,copied_source_recovery_audit_executions=1,
        original_raw_audit_reexecuted_to_fix_checker=False,verification_expanded_for_batch_order_tampering=True,
        full_cases=3,four_rank_saved_record_complete=False,four_rank_pending_tail_batches=3,
        physical_interface_implemented=False,same_ns_native_scheduler_order_verified=False,
        representative_four_rank_call_complete=False,platform_and_native_runtime_unchanged=True,
        source_native_commit=native['actual_native_commit'],dependency_checkouts_verified=13,
        all_packaged_sources_current=True,copied_python_sources_match_current=True,
        monitored_seconds=seconds,final_management_seconds_reserved=10,
        peak_sampled_group_rss_bytes=max(report['peak_sampled_group_rss_bytes'] for report in reports),
        storage_charged_bytes_including_1MiB_reserve=charged,storage_limit_bytes=96*2**20,
        unique_artifact_usage=sizes,stage_and_persistent_artifact_bytes=artifact,artifact_limit_bytes=48*2**20,
        monitor_receipts=[str(path.relative_to(stage)) for path in probes],
        final_management_receipt='runs/batched_callback_replay_checkpoint_20261025/final-management/probe.json (terminal receipt follows)',
        all_registered_commands_terminal=True,no_live_registered_process_groups=True,
        archive_members=len(members),final_archive_sha256=sha(archive),
        archive_scope='pre-closeout offline snapshot; terminal final-archive and management receipts stored separately',
        dependencies_installed_or_upgraded=False,pushed=False,
        next_requirement='separately bounded native batch transport preserving collection boundaries, original simulation times and scheduling order; then original four-rank actual execution')
    for directory in (stage,repo/'fixtures/batched_callback_replay',archive.parent):save(directory/'closeout.json',receipt)


if __name__=='__main__':main()
