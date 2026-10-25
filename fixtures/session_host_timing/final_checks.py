"""Freeze verifiable stage results before and after the fresh small execution."""
import argparse
from pathlib import Path
from fixtures.session_host_timing.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    identity=load(stage/'runtime-identity.json')
    assert all(sha(repo/path)==value for path,value in identity['sources'].items())
    assert all(sha(stage/'runtime'/path)==value for path,value in identity['files'].items())
    assert all(sha(stage/path)==value for path,value in load(stage/'inputs.json')['files'].items())
    for name in ('default-equivalence','timed-equivalence','checks'):
        assert load(stage/(name+'.json'))['passed']
    failure=load(stage/'failure-analysis.json')
    assert failure['reason']=='native observation record budget exhausted' and not failure['service_complete']
    assert 'Ran 43 tests' in (stage/'python-unit-monitor/stderr.log').read_text()
    assert load(stage/'disabled-native-observer-monitor/stdout.log')['disabled_observation_clock_calls']==0
    assert '--host-timing' in (stage/'public-help-monitor/stdout.log').read_text()
    assert load(stage/'four-public-summary.json')['all_traces_complete'] is False
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    # Preserve a launcher failure observed by the parent exec tool: public-help
    # was requested before the tamper-check process returned its live handle.
    # The guard rejected it before command registration or child execution.
    save(stage/'launcher-rejection.json',dict(observed_exit_code=1,network_started=False,
        child_command_started=False,origin='parent exec tool return',
        reason='all registered commands must be terminal; tamper-check handle still running',
        rejected_label='public-help',guard_source_sha256=sha(repo/'fixtures/session_host_timing/stage.py'),
        original_error='AssertionError at stage.py run: all(row[status]==terminal for row in records)',
        later_public_help_registered_once=True))
    records=load(stage/'commands.json');networks=[row for row in records if row['network']]
    assert len(networks) in (3,4)
    assert [row['label'] for row in networks if row['exit_code']!=0]==['four-routes']
    recovery=load(stage/'recovery.json') if (stage/'recovery.json').exists() else None
    if recovery:assert recovery['passed'] and len(networks)==4
    result=dict(verified=True,platform_observation_acceptance_complete=bool(recovery),
        representative_four_rank_call_complete=False,overall_goal_complete=False,
        actual_network_executions=len(networks),maximum_network_executions=4,
        default_full_equivalence=load(stage/'default-equivalence.json'),
        timed_full_equivalence=load(stage/'timed-equivalence.json'),four_request_failure=failure,
        distinct_python_tests=43,new_python_tests=6,disabled_cpp_observer_clock_reads=0,
        rejected_evidence_tampering=load(stage/'checks.json')['rejected'],
        decision_limit_unchanged=256,source_and_runtime_identity_checked=True,recovery=recovery,
        dependency_checkouts_verified=13,
        scope='optional bounded host observation; no protocol/model optimization, full inference or paper novelty claim')
    save(stage/'verification.json',result)
    for name in ('verification','failure-analysis','checks','default-equivalence','timed-equivalence','launcher-rejection'):
        (repo/'fixtures/session_host_timing'/(name+'.json')).write_bytes((stage/(name+'.json')).read_bytes())
    if recovery:(repo/'fixtures/session_host_timing/recovery.json').write_bytes((stage/'recovery.json').read_bytes())


if __name__=='__main__':main()
