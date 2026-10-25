"""Freeze equivalence, actual failure and immutable source/runtime identities."""
import argparse
from collections import Counter
import json
from pathlib import Path
from fixtures.incremental_progress.stage import load,save,sha


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    identity=load(stage/'runtime-identity.json')
    assert all(sha(repo/name)==value for name,value in identity['sources'].items())
    assert all(sha(stage/'runtime'/name)==value for name,value in identity['files'].items())
    assert all(sha(stage/name)==value for name,value in load(stage/'inputs.json')['files'].items())
    comparisons={name:load(stage/(name+'-verification.json')) for name in ('default-healthy','timed-healthy','wake-fault')}
    assert all(value['passed'] and value['whole_native_modern_control_equal'] for value in comparisons.values())
    four=load(stage/'four-routes-verification.json')
    assert four['original_control_prefix_byte_identical'] and not four['representative_four_rank_call_complete']
    assert four['native_execution_error']=='RDMA: session decision budget exceeded' and four['decisions']==256
    directory=stage/'four-routes';result=load(directory/'result.json');modern=load(directory/'modern-execution.json')
    plan=load(directory/'modern-plan.json')
    controls=[json.loads(line) for line in (directory/'control.jsonl').read_text().splitlines()]
    assert len(controls)==512 and len(modern['events'])==526 and len(plan['events'])==566
    fired={row['event'] for row in modern['events']}
    missing=[row for row in plan['events'] if row['id'] not in fired]
    no_submissions=[controls[index]['message']['decision_id'] for index in range(1,len(controls),2)
        if not controls[index]['message']['submissions']]
    details=dict(preserved_failure=True,full_call_complete=False,modern_events=526,required_modern_events=566,
        missing_event_kinds=dict(Counter(row['kind'] for row in missing)),missing_event_ids=[row['id'] for row in missing],
        no_submission_control_exchanges=len(no_submissions),no_submission_decision_ids=no_submissions,
        scope='no submission does not imply unnecessary exchange: prompt bookkeeping and dynamic ordering must be checked',
        completed_remote=result['completed_operations'],completed_local=result['completed_local_work'],
        registered_native_nodes=len(modern['native_operations']),finish_ns=result['finish_ns'],
        capture_complete=result['packet_capture']['complete'],capture_omitted_events=result['packet_capture']['omitted_events'],
        no_complete_physical_path_reconstruction_claim=True,
        next_requirement='causally sound control protocol for long applications preserving prompt events, source/target ownership and packet order; no increase of 256')
    save(stage/'limitation.json',details)
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    assert load(stage/'checks.json')['passed'] and len(load(stage/'checks.json')['rejected'])==6
    records=load(stage/'commands.json');networks=[row for row in records if row['network']]
    assert len(networks) in (4,5) and [row['label'] for row in networks if row['exit_code']!=0]==['four-routes']
    recovery=load(stage/'recovery.json') if (stage/'recovery.json').exists() else None
    if recovery:assert recovery['passed'] and len(networks)==5
    value=dict(verified=True,incremental_collection_acceptance_complete=bool(recovery),
        overall_goal_complete=False,representative_four_rank_call_complete=False,
        actual_network_executions=len(networks),maximum_actual_network_executions=5,
        equivalent_cases=comparisons,original_four_rank_execution=four,limitation=details,recovery=recovery,
        full_snapshot_shadow_checked_cases=['default-healthy','wake-fault'],
        dependency_checkouts_verified=13,sources_runtime_inputs_identity_checked=True,
        rejected_evidence_tampering=load(stage/'checks.json')['rejected'],
        scope='platform state-collection optimization only; not full application, calibrated GPU/provider or paper novelty')
    save(stage/'verification.json',value)
    for name in ('verification','limitation','checks','default-healthy-verification','timed-healthy-verification','wake-fault-verification','four-routes-verification'):
        (repo/'fixtures/incremental_progress'/(name+'.json')).write_bytes((stage/(name+'.json')).read_bytes())
    if recovery:(repo/'fixtures/incremental_progress/recovery.json').write_bytes((stage/'recovery.json').read_bytes())


if __name__=='__main__':main()
