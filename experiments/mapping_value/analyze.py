"""Conservative evidence screening. Reads only a validated input directory.

Port differences are associations, not a claim that every changed port caused
the slowdown. Binary relations narrow the selected call's business scope only.
"""
from collections import defaultdict
from pathlib import Path

from .common import digest, require, rows, save
from .inputs import port_key, record_key, validate, window_key


def endpoint_differences(current, reference):
    current = {record_key(r):r for r in current}; reference = {record_key(r):r for r in reference}
    require(current.keys() == reference.keys(), 'Public local-call reference sets differ')
    result = []
    for key,row in sorted(current.items()):
        old = reference[key]
        require(row['kind'] == old['kind'], 'Public local-call kinds differ')
        result.append(dict(rank_id=key[0],local_record_id=key[1],kind=row['kind'],
            duration_delta_ns=(row['end_local_ns']-row['start_local_ns'])-(old['end_local_ns']-old['start_local_ns']),
            completion_shift_ns=row['end_local_ns']-old['end_local_ns'],
            start_shift_ns=row['start_local_ns']-old['start_local_ns']))
    return result


def preparation_differences(current, reference):
    def durations(source):
        stages = {(*record_key(r),r['stage']):r['time_local_ns'] for r in source}
        return {key[:2]:end-stages[(*key[:2],'prepare_start')] for key,end in stages.items() if key[2]=='prepare_end'}
    current,reference = durations(current),durations(reference)
    require(current.keys() == reference.keys(), 'Preparation stage availability differs')
    return {k:current[k]-reference[k] for k in current}


def port_differences(current, reference, *, queue=False):
    """Streaming-compatible union of sparse windows; zeros only inside coverage."""
    fields = (['voq_waiting_mean_bytes','fifo_waiting_mean_bytes'] if queue else ['counted_packet_bytes'])
    old = {window_key(row):sum(row[k] for k in fields) for row in reference}
    score = defaultdict(float)
    for row in current:
        delta = sum(row[k] for k in fields)-old.pop(window_key(row),0)
        score[port_key(row)] += max(0,delta) if queue else abs(delta)
    if not queue:
        for key,value in old.items():
            score[key[:2]] += value
    return score


def analyze(directory, output):
    directory,output = Path(directory),Path(output)
    require(not output.exists(), 'Analysis output already exists')
    public,manifest = validate(directory)
    def table(name, reference=False):
        return rows(directory/(('reference_' if reference else '')+name+'.jsonl'))
    endpoints = list(table('endpoint')); reference = list(table('endpoint',True))
    changes = endpoint_differences(endpoints,reference)
    preparation = preparation_differences(table('stages'),table('stages',True))
    tolerance = public['time_tolerance_ns']
    affected = [r for r in changes if r['rank_id']==public['anchor_rank'] and
                max(r['duration_delta_ns'],r['completion_shift_ns']) > tolerance]
    # First visibly changed anchor call; same public rule for every condition.
    selected = min(affected,key=lambda r:r['local_record_id']) if affected else None
    endpoint_evidence = [dict(rank_id=key[0],local_record_id=key[1],preparation_delta_ns=value)
                         for key,value in sorted(preparation.items()) if value > tolerance]
    counters = port_differences(table('ports'),table('ports',True)) if manifest['condition']!='E1' else {}
    queues = port_differences(table('queues'),table('queues',True),queue=True) if manifest['condition'] in {'E4','E5','E5S'} else {}
    linked = None
    if 'relations' in {name.removesuffix('.jsonl') for name in manifest['files']} and selected is not None:
        linked = {port_key(r) for r in table('relations') if record_key(r)==record_key(selected)}
        # Normal and current relationship are equally available in mapped groups.
        linked.update(port_key(r) for r in table('relations',True) if record_key(r)==record_key(selected))
    candidates = []
    for p in public['ports']:
        key = port_key(p)
        if counters.get(key,0) <= 0:
            continue
        candidates.append(dict(switch_id=key[0],port_id=key[1],
            traffic_difference_bytes=counters[key],queue_excess_mean_sum_bytes=queues.get(key),
            related_to_selected_call=None if linked is None else key in linked))
    # Queue coverage is partial: absence of queue evidence never discards a port.
    candidates.sort(key=lambda r:(-(r['queue_excess_mean_sum_bytes'] or 0),-r['traffic_difference_bytes'],r['switch_id'],r['port_id']))
    scoped = [r for r in candidates if r['related_to_selected_call'] is not False]
    endpoint_candidates = sorted({r['rank_id'] for r in endpoint_evidence})
    changed = any(max(abs(r['duration_delta_ns']),abs(r['completion_shift_ns']))>tolerance for r in changes)
    assessment = ('endpoint_preparation_increase' if endpoint_candidates else
                  'unresolved_wait_with_network_changes' if changed and candidates else
                  'unresolved_wait' if changed else 'no_endpoint_change_above_tolerance')
    # This is an observable proxy, NOT the measured whole-round completion.
    proxy_delta = max(r['end_local_ns'] for r in endpoints)-max(r['end_local_ns'] for r in reference)
    threshold = max(public['material_round_absolute_ns'],
                    max(r['end_local_ns'] for r in reference)*public['material_round_relative'])
    value = dict(schema_version='mapping-value-analysis/1',condition=manifest['condition'],
        input_sha256=digest(directory/'condition.json'),analysis_version=public['analysis_version'],
        assessment=assessment,selected_call=None if selected is None else
            {k:selected[k] for k in ('rank_id','local_record_id','kind')},
        endpoint_candidates=endpoint_candidates,preparation_evidence=endpoint_evidence,
        port_candidates=scoped,all_changed_ports=candidates,
        excluded_from_selected_call=[r for r in candidates if r['related_to_selected_call'] is False],
        candidate_scope='selected_logical_call_business_or_declared_state_relations_not_all_possible_root_causes',
        queue_coverage=public['queue_ports'] if manifest['condition'] in {'E4','E5','E5S'} else [],
        endpoint_differences=changes,completion_proxy_delta_ns=proxy_delta,
        material_round_prediction=proxy_delta>threshold,
        qualification='Endpoint completion proxy; true round delta is measured independently by evaluation. '
                      'Traffic/queue changes alone do not establish causal root location.')
    save(output,value)
    return dict(passed=True,output=str(output),assessment=assessment,port_candidates=len(scoped))
