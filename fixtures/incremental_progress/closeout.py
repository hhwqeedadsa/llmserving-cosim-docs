"""Final source/runtime/budget audit, with original four-rank failure retained."""
import argparse
from pathlib import Path
from fixtures.context_transactions.checkpoint import admit
from fixtures.incremental_progress.stage import load,save,sha,usage,roots
from moe_ub_cosim.host_probe import disk_usage


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','archive'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve();archive=a.archive.resolve()
    records=load(stage/'commands.json');assert all(row['status']=='terminal' for row in records)
    networks=[row for row in records if row['network']]
    assert len(networks)==5 and sum(row['exit_code']==0 for row in networks)==4
    assert [row['label'] for row in records if row['exit_code']!=0]==['four-routes']
    probes=sorted(path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored')
    reports=[load(path) for path in probes]
    assert all((stage/(row['label']+'-monitor/probe.json')).is_file() for row in records)
    assert all(not report['remaining_live_group_pids'] for report in reports)
    groups={report['process_group'] for report in reports}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:fields=path.read_text().rsplit(')',1)[1].split()
        except (OSError,IndexError):continue
        assert fields[0]=='Z' or int(fields[2]) not in groups
    runtime=load(stage/'runtime-identity.json')
    assert all(sha(repo/name)==digest for name,digest in runtime['sources'].items())
    assert all(sha(stage/'runtime'/name)==digest for name,digest in runtime['files'].items())
    assert all(sha(Path(name))==digest for name,digest in {**runtime['unchanged_objects'],**runtime['libraries']}.items())
    assert all(sha(stage/name)==digest for name,digest in load(stage/'inputs.json')['files'].items())
    python=list((repo/'python/moe_ub_cosim').rglob('*.py'))
    assert python and all(sha(path)==sha(stage/'restored/source'/path.relative_to(repo)) for path in python)
    verification=load(stage/'verification.json');recovery=load(stage/'recovery.json')
    assert verification['verified'] and verification['incremental_collection_acceptance_complete'] and recovery['passed']
    assert verification['representative_four_rank_call_complete'] is False
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    content,manifest=admit(archive)
    assert all(sha(repo/name[7:])==entry['sha256'] for name,entry in manifest['files'].items() if name.startswith('source/'))
    assert all(content['evidence/runtime/'+name]==(stage/'runtime'/name).read_bytes() for name in runtime['files'])
    assert sha(archive)==load(archive.with_suffix('.receipt.json'))['sha256']
    seconds=sum(report['wall_seconds'] for report in reports);charged=usage(roots(repo,stage))+2*2**20
    seen=set();sizes=dict(apparent_bytes=0,allocated_bytes=0)
    for root in (stage,archive.parent):
        for key,value in disk_usage(root,seen).items():sizes[key]+=value
    artifact=max(sizes.values())
    assert seconds+10<600 and charged<512*2**20 and artifact+2*2**20<128*2**20
    assert all(report['wall_seconds']<60 and report['peak_sampled_group_rss_bytes']<2**30 for report in reports)
    receipt=dict(verified=True,incremental_collection_stage_status='completed',stage_execution_budget_closed=True,
        original_four_rank_acceptance_status='in_progress',overall_goal_status='in_progress',
        actual_network_executions=5,successful_network_executions=4,failed_network_executions=1,
        preserved_failure_labels=['four-routes'],network_failure_reason='256 session decisions exhausted',
        unchanged_successful_network_reexecutions=0,native_full_snapshot_shadow_checked_cases=['default-healthy','wake-fault'],
        representative_four_rank_call_complete=False,complete_four_rank_physical_capture=False,
        monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        sampled_peak_group_rss_bytes=max(report['peak_sampled_group_rss_bytes'] for report in reports),
        storage_charged_bytes_including_2MiB_reserve=charged,storage_limit_bytes=512*2**20,
        cumulative_storage_accounting='conservative path allocations; hardlinks may be counted twice',
        unique_artifact_usage=sizes,stage_and_persistent_unique_artifact_bytes=artifact,artifact_limit_bytes=128*2**20,
        monitor_receipts=[str(path.relative_to(stage)) for path in probes],
        final_management_receipt='runs/incremental_progress_checkpoint_20261025/final-management/probe.json (terminal receipt follows)',
        all_registered_commands_terminal=True,no_live_registered_process_groups=True,
        native_control_decision_limit_unchanged=256,actual_native_commit=runtime['actual_native_commit'],
        fixed_streams_fabric_and_eight_libraries_unchanged=True,all_direct_transaction_consumers_recompiled=True,
        dependency_checkouts_verified=13,all_copied_python_sources_current=True,all_packaged_sources_current=True,
        regenerable_compiler_objects_removed_with_hashes=True,original_success_and_failure_evidence_retained=True,
        final_archive_sha256=sha(archive),archive_members=len(content),
        final_archive_scope='pre-closeout snapshot; final archive monitor and terminal management receipts stored separately',
        recovery_scope=recovery['scope'],gpu_executed=False,dependencies_installed_or_upgraded=False,pushed=False,
        next_requirement='preserve original event times and registration order while reducing necessary synchronous control exchanges; no limit increase')
    for directory in (stage,repo/'fixtures/incremental_progress',archive.parent):save(directory/'closeout.json',receipt)


if __name__=='__main__':main()
