"""Collect already evaluated pairs; no simulator inputs or algorithm changes."""
from pathlib import Path

from .common import digest, read, require, save


def report(assessments, output):
    output=Path(output);require(not output.exists(),'Report output already exists')
    pairs=[];versions=set();condition_sets=[];identities=set()
    for directory in map(Path,assessments):
        summary=read(directory/'summary.json');results={}
        preparation=read(directory/'prepared/preparation.json')
        identity=preparation['run_manifest_sha256'],preparation['reference_manifest_sha256']
        require(identity not in identities,'Do not count the same physical pair twice');identities.add(identity)
        for condition in summary['results']:
            evaluation=read(directory/'evaluation'/(condition+'.json'))
            analysis=read(directory/'analysis'/(condition+'.json'))
            require(evaluation['analysis_sha256']==digest(directory/'analysis'/(condition+'.json')) and
                    evaluation['run_manifest_sha256']==preparation['run_manifest_sha256'] and
                    evaluation['reference_manifest_sha256']==preparation['reference_manifest_sha256'],
                    'Assessment evidence binding differs')
            versions.add(analysis['analysis_version'])
            results[condition]={k:evaluation[k] for k in ('assessment','port_candidate_count','port_target_ranks',
                'endpoint_candidates','material_prediction_correct','port_evaluation_status',
                'port_target_in_candidates','selected_call_target_wrongly_excluded')}
        condition_sets.append(set(results))
        require(len({read(directory/'evaluation'/(c+'.json'))['round_delta_ns'] for c in results})==1,
                'Information conditions must share one physical pair')
        pairs.append(dict(assessment=str(directory),run_manifest_sha256=preparation['run_manifest_sha256'],
            reference_manifest_sha256=preparation['reference_manifest_sha256'],
            round_delta_ns=evaluation['round_delta_ns'],material_round_effect=evaluation['material_round_effect'],
            application_window_hit=evaluation['application_window_hit'],conditions=results,
            mapping_candidate_reduction_E3_vs_E2=results['E2']['port_candidate_count']-results['E3']['port_candidate_count'],
            mapping_candidate_reduction_E5_vs_E4=(results['E4']['port_candidate_count']-results['E5']['port_candidate_count']) if 'E5' in results else None))
    require(pairs and len(versions)==1 and all(s==condition_sets[0] for s in condition_sets),
            'Do not combine method versions or unequal information conditions')
    value=dict(schema_version='mapping-value-report/1',analysis_version=next(iter(versions)),pairs=pairs,
        independent_random_workloads=None,independence_status='not_inferred_from_pair_or_request_count',physical_pairs=len(pairs),
        calibration_status='unvalidated_reference',
        qualification='Paired mechanisms on one fixed workload, not independent request samples or statistical significance. '
                      'Mapping candidate scope and enhanced observation ranking are reported separately.')
    save(output,value)
    return dict(passed=True,output=str(output),pairs=len(pairs),analysis_version=value['analysis_version'])
