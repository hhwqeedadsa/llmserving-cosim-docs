"""Seal terminal evidence and fixed budgets; never start or replay a network."""
import argparse
from pathlib import Path

from fixtures.context_transactions.checkpoint import admit
from fixtures.session_host_timing.stage import load, roots, save, sha, usage
from moe_ub_cosim.host_probe import disk_usage


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage','archive'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    repo=args.repository.resolve();stage=args.stage.resolve();archive=args.archive.resolve()
    journal=load(stage/'commands.json')
    assert all(row['status']=='terminal' for row in journal)
    networks=[row for row in journal if row['network']]
    assert len(networks)==4 and sum(row['exit_code']==0 for row in networks)==3
    assert [row['label'] for row in journal if row['exit_code']!=0]==['four-routes']
    probes=sorted(path for path in stage.rglob('probe.json') if path.relative_to(stage).parts[0]!='restored')
    reports=[load(path) for path in probes]
    assert all((stage/(row['label']+'-monitor/probe.json')).is_file() for row in journal)
    assert all(not report['remaining_live_group_pids'] for report in reports)
    groups={report['process_group'] for report in reports}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:fields=path.read_text().rsplit(')',1)[1].split()
        except (OSError,IndexError):continue
        assert fields[0]=='Z' or int(fields[2]) not in groups
    identity=load(stage/'identity.json');runtime=load(stage/'runtime-identity.json')
    assert all(sha(Path(name))==digest for name,digest in identity['unchanged_objects'].items())
    assert all(sha(Path(name))==digest for name,digest in identity['libraries'].items())
    assert all(sha(repo/name)==digest for name,digest in runtime['sources'].items())
    assert all(sha(stage/'runtime'/name)==digest for name,digest in runtime['files'].items())
    assert all(sha(stage/name)==digest for name,digest in load(stage/'inputs.json')['files'].items())
    python_files=list((repo/'python/moe_ub_cosim').rglob('*.py'))
    assert python_files and all(sha(path)==sha(stage/'restored/source'/path.relative_to(repo)) for path in python_files)
    verification=load(stage/'verification.json');recovery=load(stage/'recovery.json')
    assert verification['verified'] and verification['platform_observation_acceptance_complete']
    assert verification['representative_four_rank_call_complete'] is False and recovery['passed']
    assert verification['actual_network_executions']==4
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    rejected=load(stage/'launcher-rejection.json')
    assert rejected['network_started'] is False and rejected['child_command_started'] is False
    content,manifest=admit(archive)
    assert all(sha(repo/name[7:])==entry['sha256'] for name,entry in manifest['files'].items() if name.startswith('source/'))
    assert all(content['evidence/runtime/'+name]==(stage/'runtime'/name).read_bytes() for name in runtime['files'])
    assert sha(archive)==load(archive.with_suffix('.receipt.json'))['sha256']
    seconds=sum(report['wall_seconds'] for report in reports)
    charged=usage(roots(repo,stage))+2*2**20
    # Match host_probe: share inode identities across both artifact roots.
    # Cumulative storage above deliberately remains conservative per path.
    seen=set();totals=dict(apparent_bytes=0,allocated_bytes=0)
    for root in (stage,archive.parent):
        for key,value in disk_usage(root,seen).items():totals[key]+=value
    artifact=max(totals.values())
    assert seconds+10<600 and charged<512*2**20 and artifact+2*2**20<128*2**20
    assert all(report['wall_seconds']<60 and report['peak_sampled_group_rss_bytes']<2**30 for report in reports)
    receipt=dict(verified=True,platform_observation_stage_status='completed',overall_goal_status='in_progress',
        stage_execution_budget_closed=True,actual_network_executions=4,successful_network_executions=3,
        failed_network_executions=1,preserved_network_failure_labels=['four-routes'],
        network_failure_reason='native observation record budget exhausted',
        prelaunch_guard_rejections=1,prelaunch_guard_rejection_label='public-help',
        successful_network_reexecutions=0,representative_four_rank_call_complete=False,
        monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        sampled_peak_group_rss_bytes=max(report['peak_sampled_group_rss_bytes'] for report in reports),
        storage_charged_bytes_including_2MiB_reserve=charged,storage_limit_bytes=512*2**20,
        storage_accounting='conservative path allocations; restored immutable hardlinks may be charged twice',
        artifact_usage=totals,stage_and_persistent_unique_artifact_bytes=artifact,artifact_limit_bytes=128*2**20,
        monitor_receipts=[str(path.relative_to(stage)) for path in probes],
        final_management_receipt='runs/session_host_timing_checkpoint_20261024/final-management/probe.json (written after this receipt)',
        all_registered_commands_terminal=True,no_live_registered_process_groups=True,
        native_control_decision_limit_unchanged=256,actual_native_commit=runtime['actual_native_commit'],
        fixed_objects_runtime_and_eight_libraries_unchanged=True,dependency_checkouts_verified=13,
        all_copied_python_sources_current=True,all_packaged_sources_current=True,
        final_archive_sha256=sha(archive),archive_members=len(content),
        final_archive_scope='pre-closeout snapshot; final archive monitor and closeout receipts stored separately',
        recovery_scope=recovery['scope'],gpu_executed=False,dependencies_installed_or_upgraded=False,pushed=False,
        next_requirement='separately bounded incremental operation-transition collection preserving callback time/order and original four-rank workload')
    for directory in (stage,repo/'fixtures/session_host_timing',archive.parent):save(directory/'closeout.json',receipt)


if __name__=='__main__':main()
