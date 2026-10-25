"""Freeze offline results and prove no platform/runtime source was changed."""
import argparse
from pathlib import Path
from fixtures.batched_callback_replay.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve();identity=load(stage/'identity.json')
    assert all(sha(repo/name)==digest for name,digest in identity['platform_sources'].items())
    native=identity['native_runtime'];previous=Path(identity['source_stage'])
    assert all(sha(repo/name)==digest for name,digest in native['sources'].items())
    assert all(sha(previous/'runtime'/name)==digest for name,digest in native['files'].items())
    assert all(sha(stage/name)==digest for name,digest in load(stage/'inputs.json')['files'].items())
    verified=load(stage/'verification-final.json');assert verified['verified'] and len(verified['tamper_rejections'])==9
    assert verified['cases']['four-routes']['source_record_complete'] is False
    assert 'Ran 14 tests' in (stage/'unit-monitor/stderr.log').read_text()
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    assert all(not row['network'] for row in load(stage/'commands.json'))
    recovery=load(stage/'recovery.json') if (stage/'recovery.json').exists() else None
    if recovery:assert recovery['passed'] and recovery['network_executed'] is False
    summary=dict(verified=True,offline_candidate_accepted_for_native_design=True,offline_recovery_complete=bool(recovery),
        original_logical_decision_limit_unchanged=256,platform_sources_and_native_runtime_unchanged=True,
        python_tests=14,new_python_tests=6,dependency_checkouts_verified=13,tamper_rejections=verified['tamper_rejections'],
        cases=verified['cases'],physical_interface_implemented=False,same_ns_native_scheduler_order_verified=False,
        representative_four_rank_call_complete=False,overall_goal_complete=False,network_executed=False,
        scope=verified['scope'],recovery=recovery)
    save(stage/'summary.json',summary)
    for name,source in (('summary','summary'),('verification','verification-final'),('identity','identity')):
        (repo/'fixtures/batched_callback_replay'/(name+'.json')).write_bytes((stage/(source+'.json')).read_bytes())
    if recovery:(repo/'fixtures/batched_callback_replay/recovery.json').write_bytes((stage/'recovery.json').read_bytes())


if __name__=='__main__':main()
