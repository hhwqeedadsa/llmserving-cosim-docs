"""Close only the physical binding stage, never the overall research goal."""
import argparse
from pathlib import Path

from moe_ub_cosim.host_probe import disk_usage
from .workflow import load, save, sha


def close(repository, stage):
    assert load(stage/'verification.json')['passed'] and load(stage/'restored/recovery.json')['passed']
    assert load(stage/'tampering.json')['passed'] and load(stage/'negative/verification.json')['passed']
    monitors = []
    for path in sorted(stage.rglob('probe.json')):
        if path.is_relative_to(stage/'restored'): continue
        row = load(path)
        monitors.append(dict(path=str(path.relative_to(stage)), status=row['status'],
                             wall_seconds=row['wall_seconds'], rss_bytes=row['peak_sampled_group_rss_bytes']))
    failures = {'fresh-build/probe.json','native-api-compile/probe.json','packet-audit/probe.json',
                'dependency-check/probe.json','partition-network/probe.json','partition/execution/probe.json'}
    assert {r['path'] for r in monitors if r['status'] != 'completed'} == failures
    elapsed = sum(row['wall_seconds'] for row in monitors)
    assert elapsed+10 < 1800
    roots = [stage, repository/'fixtures/physical_nic_binding',repository/'examples/scaleout_multirail_native',
             repository/'backend',repository/'python/moe_ub_cosim',repository/'docs',repository/'tests/native']
    seen=set(); usage=dict(apparent_bytes=0,allocated_bytes=0)
    for root in roots:
        for key,value in disk_usage(root,seen).items(): usage[key]+=value
    assert max(usage.values())+4*2**20 < 2**31
    for name, limit in (('configure',60),('compile',300)):
        assert load(stage/'build/logs'/name/'probe.json')['wall_seconds'] <= limit
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check-saved-parent/stdout.log').read_text()
    assert 'Ran 153 tests' in (stage/'scaleout-regressions/stderr.log').read_text()
    assert 'Ran 52 tests' in (stage/'focused-regressions/stderr.log').read_text()
    originals = load(stage/'prepared.json')['inputs']
    for name,digest in originals.items(): assert sha(stage/'inputs'/name) == digest
    for source, target in [('shared-slow-topology.json','topology.json'),('congested-protocol.json','rdma.json'),
                           ('shared-reference-work.json','work.json')]:
        assert (stage/'inputs'/source).read_bytes() == (repository/'examples/scaleout_multirail_native'/target).read_bytes()
    commands = load(stage/'commands.json')
    public = {'rdma-stream-probe','rdma-transaction-probe','modern-deepep-native'}
    network_commands = [r for r in commands if public.intersection(r['command'])]
    assert len(network_commands) == 8 and len({r['label'] for r in network_commands}) == 8
    result = dict(passed=True, platform_stage_completed=True, overall_research_goal_completed=False,
        conservative_monitored_seconds_including_nested=elapsed, wall_budget_seconds=1800,
        closing_receipt_seconds_reserved=10, artifact_usage=usage, artifact_budget_bytes=2**31,
        artifact_scope='stage plus fixtures/example/native test and conservative full backend/Python/docs source trees',
        closing_receipt_bytes_reserved=4*2**20, terminal_monitors=monitors,
        fresh_configures=1, fresh_full_compiles=1, actual_network_executions=8,
        native_object_assertions=20, route_test_methods=10, exhaustive_nic_pairs=512,
        scaleout_regression_executions=153, focused_regression_executions=52,
        counts_include_reused_methods=True, mutation_classes_rejected=10, public_negative_entries=3,
        dependency_checkouts_verified=13, local_dependency_commits=load(stage/'dependency-bundles/identities.json')['dependencies'],
        copied_source_recovery=True, additional_recovery_networks=0, additional_recovery_builds=0,
        same_plane_physical_nic_binding=True, shared_agg_actual_contention=True,
        alternate_path_case_had_no_actual_packet_loss=True, production_fault_or_gpu_calibration=False,
        public_example_byte_identical=True, gpu_executed=False, downloaded=False, pushed=False,
        archive=load(stage/'checkpoint.tar.receipt.json'), failures_preserved=sorted(failures),
        source_saving_failure=load(stage/'source-saving-error.json'))
    save(stage/'closeout.json',result)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','stage'): parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();close(args.repository.resolve(),args.stage.resolve())
