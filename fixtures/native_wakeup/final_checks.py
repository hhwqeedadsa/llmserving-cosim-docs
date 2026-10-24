"""No networks: reconcile final recovery, source identity and incomplete acceptance."""
import argparse
import ast
from pathlib import Path
from fixtures.native_wakeup.stage import load, save, sha


def executable_tree(path):
    tree=ast.parse(path.read_text())
    for node in ast.walk(tree):
        body=getattr(node,'body',None)
        if isinstance(body,list) and body and isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and isinstance(body[0].value.value,str):
            body.pop(0)
    return ast.dump(tree,include_attributes=False)


def main():
    parser=argparse.ArgumentParser()
    for name in ('repository','stage'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();repo=args.repository.resolve();stage=args.stage.resolve()
    source_changes={}
    for name,before in load(stage/'python-before-docs.json').items():
        saved=stage/'python-before-docs'/name;current=repo/'python/moe_ub_cosim'/name
        assert sha(saved)==before
        assert executable_tree(saved)==executable_tree(current)
        source_changes[name]=dict(before_sha256=before,final_sha256=sha(current),executable_ast_equal=True)
    build=load(stage/'runtime-identity.json')
    assert all(sha(repo/path)==value for path,value in build['sources'].items())
    assert all(sha(stage/'runtime'/path)==value for path,value in build['files'].items())
    assert '--callback-wakeups' in (stage/'public-help-monitor/stdout.log').read_text()
    checks=load(stage/'checks.json');assert checks['passed'] and len(checks['rejected_evidence_tampering'])==6
    failure=load(stage/'failure-analysis.json');assert not failure['four_request_full_call_passed']
    assert not (stage/'four-routes/result.json').exists()
    recovery=load(stage/'recovery.json')
    assert recovery['passed'] and recovery['fixed_runtime_and_eight_library_hashes_checked']
    assert recovery['fresh_modern_acceptance']['modern_events']==152
    assert recovery['fresh_modern_acceptance']['decisions']==45
    assert not recovery['four_request_full_call_passed']
    proof=load(stage/'fresh-input-admission-rejection.json')
    assert proof['native_network_started'] is False
    assert sha(stage/'fresh-input.json')==proof['failed_input_sha256']
    records=load(stage/'commands.json')
    attempts=[row for row in records if row['network']]
    rejected=[row for row in attempts if row.get('network_started') is False]
    actual=[row for row in attempts if row.get('network_started',True)]
    assert len(attempts)==7 and len(actual)==6 and len(rejected)==1
    assert rejected[0]['label']=='restored-smoke' and rejected[0]['exit_code']==1
    assert all(row['status']=='terminal' for row in attempts)
    assert [row['label'] for row in actual if row['exit_code']!=0]==['four-routes']
    dependency=load(stage/'dependency-check-monitor/probe.json')
    assert dependency['status']=='completed' and dependency['child_exit_code']==0
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependency-check-monitor/stdout.log').read_text()
    fresh_root=Path(recovery['fresh_python_root'])
    sources={}
    for path in sorted((repo/'python/moe_ub_cosim').rglob('*.py')):
        relative=path.relative_to(repo/'python')
        assert sha(path)==sha(fresh_root/relative)
        sources[str(relative)]=sha(path)
    for name,digest in recovery['original_three_verifications_byte_identical'].items():
        assert sha(stage/('fresh-'+name+'-verification.json'))==digest
    if not (stage/'verification-before-recovery.json').exists():
        (stage/'verification-before-recovery.json').write_bytes((stage/'verification.json').read_bytes())
    save(stage/'source-equivalence.json',dict(passed=True,docstring_only_changes=source_changes,native_sources_and_runtime_unchanged=True))
    save(stage/'verification.json',dict(verified=True,acceptance_complete=False,
        complete_cases={name:load(stage/(name+'.json')) for name in ('default-equivalence','healthy-equivalence','fault-equivalence','control-verification')},
        four_request_failure=failure,source_changes=source_changes,
        cplusplus_checks=44,native_admission_rejections=20,new_python_tests=5,affected_existing_python_tests=26,
        tamper_rejections=checks['rejected_evidence_tampering'],networks_so_far=6,maximum_networks=6,
        actual_successful_networks=5,actual_failed_networks=1,declared_network_attempts=7,
        prelaunch_admission_rejections=1,remaining_network=None,
        recovery=recovery,fresh_source_hashes=sources,dependency_checkouts_verified=13,
        stage_execution_budget_closed=True,
        decision_limit=256,hardware_calibrated=False,overall_goal_complete=False))
    for name in ('verification','source-equivalence','failure-analysis','host-profile','recovery',
                 'checks','control-verification','fresh-input-admission-rejection','fresh-driver-amendment'):
        (repo/'fixtures/native_wakeup'/(name+'.json')).write_bytes((stage/(name+'.json')).read_bytes())


if __name__=='__main__':main()
