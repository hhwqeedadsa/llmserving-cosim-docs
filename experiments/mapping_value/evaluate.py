"""Paired evaluation can read truth; analysis never imports this module."""
from pathlib import Path

from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save


def paired_operations(run, reference):
    def index(source):
        result = {}
        for operation in stored_rows(source/'truth','operation_registry.jsonl'):
            for ep in operation['endpoint_records']:
                key = operation['operation_id'],ep['rank_id']
                require(key not in result,'Duplicate evaluation operation identity')
                result[key] = (operation,ep)
        return result
    current,normal = index(run),index(reference)
    require(current.keys() == normal.keys(), 'Paired logical operation sets differ')
    result = []
    for key,(operation,ep) in sorted(current.items()):
        previous,old = normal[key]
        require(all(operation[k]==previous[k] for k in ('group_id','kind','layer_id','participants')), 'Paired operation semantics differ')
        result.append(dict(operation_id=key[0],rank_id=key[1],kind=operation['kind'],layer_id=operation['layer_id'],
            local_record_id=ep['local_record_id'],
            duration_delta_ns=(ep['end_global_ns']-ep['start_global_ns'])-(old['end_global_ns']-old['start_global_ns']),
            completion_shift_ns=ep['end_global_ns']-old['end_global_ns'],
            start_shift_ns=ep['start_global_ns']-old['start_global_ns']))
    return result


def evaluate(run, reference, analysis, case, config, prepared, output):
    run,reference,output = map(Path,(run,reference,output))
    require(not output.exists(), 'Evaluation output already exists')
    prediction,intervention,design = read(analysis),read(case),read(config)
    preparation = read(Path(prepared)/'preparation.json')
    require(preparation['run_manifest_sha256']==digest(run/'manifest.json') and
            preparation['reference_manifest_sha256']==digest(reference/'manifest.json') and
            preparation['design_sha256']==digest(config) and
            preparation['input_sha256'][prediction['condition']]==prediction['input_sha256'],
            'Analysis/preparation is not bound to this pair and design')
    require(intervention.get('paired_reference_manifest_sha256',intervention['normal_manifest_sha256'])==digest(reference/'manifest.json'), 'Case paired reference differs')
    from .pairing import check_pair
    pairing = check_pair(run,reference,intervention)
    reports = [read(p/'raw/execution/result.json') for p in (run,reference)]
    for report in reports:
        require(report['normal_exit'] and report['network_drain']['idle'], 'Evaluation requires complete executions')
    require(reports[0]['random']==reports[1]['random'], 'Paired random configuration differs')
    delta = reports[0]['service_finished_ns']-reports[1]['service_finished_ns']
    threshold = max(design['material_round_absolute_ns'],reports[1]['service_finished_ns']*design['material_round_relative'])
    endpoints = paired_operations(run,reference)
    components=intervention.get('evaluated_components',['endpoint_prepare','port_service'])
    targets = intervention.get('endpoint_prepare',[]) if 'endpoint_prepare' in components else []
    intervals = intervention.get('port_service',[]) if 'port_service' in components else []
    endpoint_targets = sorted({r['rank_id'] for r in targets})
    port_targets = sorted({(r['node_id'],r['port_id']) for r in intervals})
    # Applied resources and actual application traffic are separate facts.
    hits = []; categories = {}
    for row in stored_rows(run/'truth','message_attribution_windows.csv'):
        for interval in intervals:
            if (row['switch_id'],row['port_id']) != (interval['node_id'],interval['port_id']):
                continue
            left,right = row['local_window_start_ns'],row['local_window_end_ns']
            if left < interval['end_ns'] and right > interval['start_ns']:
                # Frozen intervals are aligned to the observation windows.
                require(interval['start_ns']<=left and right<=interval['end_ns'], 'Evaluation interval must align to captured windows')
                categories[row['message_category']] = categories.get(row['message_category'],0)+row['application_bytes']
                hits.append(row['operation_id'])
    selected = prediction['selected_call']; selected_id = None
    if selected is not None:
        matches = [r for r in endpoints if r['rank_id']==selected['rank_id'] and r['local_record_id']==selected['local_record_id']]
        require(len(matches)==1, 'Selected public call cannot be evaluated uniquely')
        selected_id = matches[0]['operation_id']
    candidates = {(r['switch_id'],r['port_id']) for r in prediction['port_candidates']}
    ordered=[(r['switch_id'],r['port_id']) for r in prediction['port_candidates']]
    all_candidates = {(r['switch_id'],r['port_id']) for r in prediction['all_changed_ports']}
    selected_hit = selected_id in hits
    public = read(Path(prepared)/prediction['condition']/'public.json')
    observed_ports = {(p['switch_id'],p['port_id']) for p in public['ports']}
    port_evaluable = prediction['condition']!='E1' and selected is not None and set(port_targets)<=observed_ports
    status = ('network_observations_unavailable' if prediction['condition']=='E1' else
              'no_selected_call' if selected is None else
              'target_port_outside_observed_scope' if not set(port_targets)<=observed_ports else 'selected_call_scope_only')
    value = dict(schema_version='mapping-value-evaluation/1',condition=prediction['condition'],
        analysis_sha256=digest(analysis),case_sha256=digest(case),pairing=pairing,
        run_manifest_sha256=digest(run/'manifest.json'),reference_manifest_sha256=digest(reference/'manifest.json'),
        normal_round_ns=reports[1]['service_finished_ns'],round_ns=reports[0]['service_finished_ns'],
        round_delta_ns=delta,material_threshold_ns=threshold,material_round_effect=delta>threshold,
        material_prediction_correct=prediction['material_round_prediction']==(delta>threshold),
        operation_differences=endpoints,
        endpoint_target_ranks=endpoint_targets,endpoint_candidates=prediction['endpoint_candidates'],
        endpoint_target_in_candidates=None if not endpoint_targets else set(endpoint_targets)<=set(prediction['endpoint_candidates']),
        port_targets=[list(p) for p in port_targets],port_candidate_count=None if prediction['condition']=='E1' else len(candidates),
        port_evaluation_status=status,
        changed_port_count=len(all_candidates),selected_scope_reduction=len(all_candidates)-len(candidates),
        application_categories_in_intervention_windows=categories,
        application_window_hit=bool(hits),selected_call_hit=selected_hit,
        port_target_in_candidates=None if not port_targets or not port_evaluable else set(port_targets)<=candidates,
        port_target_ranks=None if not port_targets or not port_evaluable else
            [ordered.index(p)+1 if p in ordered else None for p in port_targets],
        selected_call_target_wrongly_excluded=port_evaluable and selected_hit and not set(port_targets)<=candidates,
        assessment=prediction['assessment'],
        interpretation='Resource declarations are not impact labels. Packet/control-only hits are not inferred from application attribution. '
                       'Candidate pruning concerns the selected logical call, not every downstream root cause. '
                       'Do not sum completion shifts to obtain round loss.')
    save(output,value)
    return dict(passed=True,output=str(output),round_delta_ns=delta,material_round_effect=delta>threshold)
