"""Seal this bounded stage from terminal receipts; never execute a network."""
import argparse
from pathlib import Path

from fixtures.context_transactions.checkpoint import admit
from fixtures.native_wakeup.stage import load, roots, save, sha, usage
from moe_ub_cosim.host_probe import disk_usage


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage','archive'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    repo=args.repository.resolve();stage=args.stage.resolve();archive=args.archive.resolve()
    records=load(stage/'commands.json')
    assert all(row['status']=='terminal' for row in records)
    probes=sorted(p for p in stage.rglob('probe.json') if p.relative_to(stage).parts[0]!='restored')
    external=sorted(archive.parent.rglob('probe.json'))
    reports=[load(path) for path in [*probes,*external]]
    assert all(not row['remaining_live_group_pids'] for row in reports)
    assert all((stage/(row['label']+'-monitor/probe.json')).is_file() for row in records)
    groups={row['process_group'] for row in reports}
    live=[]
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields=path.read_text().rsplit(')',1)[1].split()
        except (OSError,IndexError):
            continue
        if fields[0]!='Z' and int(fields[2]) in groups:
            live.append(int(path.parent.name))
    assert not live
    seconds=sum(row['wall_seconds'] for row in reports)
    charged=usage(roots(repo,stage))+2*2**20
    # Match the actual monitor: restored immutable files are owned hardlinks.
    # The cumulative 512MiB charge above remains conservatively per-path.
    seen=set();totals=dict(apparent_bytes=0,allocated_bytes=0)
    for directory in (stage,archive.parent):
        for name,value in disk_usage(directory,seen).items():totals[name]+=value
    artifact=max(totals.values())
    assert seconds+10<600 and charged<512*2**20 and artifact+2*2**20<128*2**20
    attempts=[row for row in records if row['network']]
    actual=[row for row in attempts if row.get('network_started',True)]
    assert len(attempts)==7 and len(actual)==6
    assert sum(row['exit_code']==0 for row in actual)==5
    assert [row['label'] for row in actual if row['exit_code']!=0]==['four-routes']
    verification=load(stage/'verification.json')
    assert verification['verified'] and not verification['acceptance_complete']
    assert verification['networks_so_far']==6 and verification['remaining_network'] is None
    members,manifest=admit(archive)
    sources={name[7:]:entry['sha256'] for name,entry in manifest['files'].items() if name.startswith('source/')}
    amendments={name:dict(archived_sha256=digest,current_sha256=sha(repo/name))
                for name,digest in sources.items() if sha(repo/name)!=digest}
    assert set(amendments)=={'fixtures/native_wakeup/closeout.py'}
    amendments['fixtures/native_wakeup/closeout.py']['reason']='match existing inode-deduplicated artifact monitor after false per-path limit rejection; no platform/runtime/network change'
    runtime=load(stage/'runtime-identity.json')
    assert all(sha(repo/name)==digest for name,digest in runtime['sources'].items())
    assert all(sha(stage/'runtime'/name)==digest for name,digest in runtime['files'].items())
    receipt=dict(verified=True,acceptance_complete=False,stage_status='in_progress',goal_status='in_progress',
        stage_execution_budget_closed=True,actual_network_executions=6,successful_network_executions=5,
        failed_network_executions=1,declared_launch_attempts=7,prelaunch_admission_rejections=1,
        successful_network_reexecutions=0,preserved_failure_labels=['four-routes','restored-smoke','final-management'],
        monitored_seconds_including_nested=seconds,final_management_seconds_reserved=10,
        final_management_receipt='runs/native_wakeup_checkpoint_20261024/final-management-corrected/probe.json (written after this receipt)',
        sampled_peak_group_rss_bytes=max(row['peak_sampled_group_rss_bytes'] for row in reports),
        storage_charged_bytes_including_2MiB_reserve=charged,storage_limit_bytes=512*2**20,
        storage_accounting='conservative path allocations; owned restored hardlinks may be charged twice',
        stage_and_persistent_artifact_bytes=artifact,artifact_limit_bytes=128*2**20,
        monitor_receipts=[str(path.relative_to(stage)) for path in probes],
        external_terminal_management_receipts=[str(path.relative_to(repo)) for path in external],
        missing_inner_monitor='four-routes/execution/probe.json: outer wall-limit killed worker and its monitor; outer terminal receipt retained',
        all_recorded_outer_monitors_terminal=True,no_remaining_recorded_process_groups=True,
        native_control_decision_limit_unchanged=256,actual_native_commit=runtime['actual_native_commit'],
        final_archive_sha256=sha(archive),archive_members=len(members),all_packaged_sources_current=False,
        all_packaged_platform_sources_current=True,postarchive_management_only_amendments=amendments,
        final_archive_scope='snapshot before final archive monitor/closeout; terminal management receipts stored separately',
        fixed_runtime_and_eight_libraries_unchanged=True,dependency_checkouts_verified=13,
        dependencies_installed_or_upgraded=False,gpu_executed=False,pushed=False,
        scope='verified two-rank callback equivalence and fresh small recovery; four-rank complete call still failed')
    for directory in (stage,repo/'fixtures/native_wakeup',archive.parent):
        save(directory/'closeout.json',receipt)
    (archive.parent/'closeout-corrected.py').write_bytes(Path(__file__).read_bytes())


if __name__=='__main__':
    main()
