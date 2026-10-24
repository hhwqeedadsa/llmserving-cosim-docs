"""Check terminal commands, source identity and fixed limits without rerunning CPU work."""
import argparse
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha, usage


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    persistent=repo/'runs/native_admission_cost_20261024'
    journal=load(stage/'commands.json')
    assert all(row['status']=='terminal' and row['exit_code']==0 and row['network'] is False for row in journal)
    assert sum(row['label']=='measurement' for row in journal)==1
    probes=sorted(stage.glob('*-monitor/probe.json'));reports=[load(path) for path in probes]
    assert all((stage/(row['label']+'-monitor/probe.json')).is_file() for row in journal)
    assert all(row['status']=='completed' and not row['remaining_live_group_pids'] for row in reports)
    groups={row['process_group'] for row in reports}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:fields=path.read_text().rsplit(')',1)[1].split()
        except (OSError,IndexError):continue
        assert fields[0]=='Z' or int(fields[2]) not in groups
    identity=load(stage/'identity.json')
    assert all(sha(Path(name))==digest for name,digest in identity['fixed_link_inputs'].items())
    assert all(sha(repo/name)==digest for name,digest in identity['native_headers'].items())
    assert sha(repo/'fixtures/native_admission_cost/measure.cc')==identity['driver_sha256']
    snapshot=persistent/'snapshot'
    manifest=load(snapshot/'manifest.json')
    assert all(sha(snapshot/name)==row['sha256'] for name,row in manifest['files'].items())
    for name in ('verify.py','measure.cc','execute.py','prepare.py','snapshot.py'):
        relative=Path('fixtures/native_admission_cost')/name
        assert sha(repo/relative)==sha(snapshot/'source'/relative)
    assert sha(stage/'measure')==sha(snapshot/'evidence/measure')
    assert load(stage/'verification.json')['passed'] and load(stage/'recovery.json')['verification_byte_identical']
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    seconds=sum(row['wall_seconds'] for row in reports)
    roots=[stage,persistent,repo/'fixtures/native_admission_cost',repo/'docs/tasks/native_admission_cost_spec.md',
           repo/'docs/reviews/四请求超时与原生准入成本_20261024.md',repo/'docs/tasks/current.md']
    charged=usage(roots)+2**20;artifacts=usage([stage,persistent])
    assert seconds+10<240 and charged<128*2**20 and artifacts+2**20<64*2**20
    receipt=dict(passed=True,diagnostic_stage_status='completed',overall_goal_status='in_progress',
        offline_candidate_sequences_executed=1,candidates=238,network_executions=0,
        measurement_reexecuted=False,platform_sources_unchanged=True,fixed_objects_and_eight_libraries_unchanged=True,
        diagnostic_executable_sha256=sha(stage/'measure'),dependency_checkouts_verified=13,
        copied_source_verification_byte_identical=True,snapshot_members=len(manifest['files']),
        snapshot_scope=manifest['scope'],snapshot_manifest_sha256=sha(snapshot/'manifest.json'),
        snapshot_precedes_closeout=True,
        monitored_seconds=seconds,final_management_seconds_reserved=10,
        peak_sampled_group_rss_bytes=max(row['peak_sampled_group_rss_bytes'] for row in reports),
        storage_charged_bytes_including_1MiB_reserve=charged,storage_limit_bytes=128*2**20,
        artifact_bytes=artifacts,artifact_limit_bytes=64*2**20,
        monitor_receipts=[str(path.relative_to(stage)) for path in probes],
        final_management_receipt='closeout-monitor/probe.json (terminal receipt follows)',
        all_registered_commands_terminal=True,no_live_registered_process_groups=True,
        gpu_executed=False,dependencies_installed_or_upgraded=False,pushed=False,
        representative_four_rank_call_complete=False,
        next_requirement='bounded live frontend/native progress/I-O/packet-event measurements; not removal of admission checks')
    for name in ('identity','verification','recovery'):
        (repo/'fixtures/native_admission_cost'/(name+'.json')).write_bytes((stage/(name+'.json')).read_bytes())
    for directory in (stage,repo/'fixtures/native_admission_cost',persistent):save(directory/'closeout.json',receipt)


if __name__=='__main__':main()
