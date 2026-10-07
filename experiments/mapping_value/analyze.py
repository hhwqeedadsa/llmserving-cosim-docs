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


def select_call(changes, anchor_rank, tolerance):
    """Infer a call's ordinal from public rank sequences; do not use hidden pairing."""
    sequences = defaultdict(list)
    for row in changes:
        sequences[row['rank_id']].append((row['local_record_id'],row['kind']))
    require(all(sorted(v)==sorted(sequences[anchor_rank]) for v in sequences.values()),
            'Public call-order heuristic requires matching per-rank call sequences')
    affected = {(r['local_record_id'],r['kind']) for r in changes
                if max(r['duration_delta_ns'],r['completion_shift_ns']) > tolerance}
    anchors = [r for r in changes if r['rank_id']==anchor_rank and (r['local_record_id'],r['kind']) in affected]
    return min(anchors,key=lambda r:r['local_record_id']) if anchors else None


def window_differences(current, reference, *, queue=False):
    fields = (['voq_waiting_mean_bytes','fifo_waiting_mean_bytes'] if queue else ['counted_packet_bytes'])
    old = {window_key(row):sum(row[k] for k in fields) for row in reference}
    result = {}
    for row in current:
        key = window_key(row)
        delta = sum(row[k] for k in fields)-old.pop(key,0)
        value = max(0,delta)*(key[3]-key[2]) if queue else abs(delta)
        if value:
            result[key] = value
    if not queue:
        result.update({key:value for key,value in old.items() if value})
    return result


def temporal_scope(selected, endpoints, reference, stages, reference_stages):
    """A public call-order heuristic, not an injected cross-rank matching answer."""
    if selected is None:
        return None
    matching = [r for r in endpoints+reference if r['local_record_id']==selected['local_record_id']
                and r['kind']==selected['kind']]
    keys = {record_key(r) for r in matching}
    times = [t for r in matching for t in (r['start_local_ns'],r['end_local_ns'])]
    times.extend(r['time_local_ns'] for r in stages+reference_stages if record_key(r) in keys)
    return min(times),max(times)+1


def related_windows(source, selected):
    if selected is None:
        return set()
    return {window_key(r) for r in source if record_key(r)==record_key(selected)}


def analyze(directory, output):
    directory,output = Path(directory),Path(output)
    require(not output.exists(), 'Analysis output already exists')
    public,manifest = validate(directory)
    def table(name, reference=False):
        return rows(directory/(('reference_' if reference else '')+name+'.jsonl'))
    endpoints = list(table('endpoint')); reference = list(table('endpoint',True))
    changes = endpoint_differences(endpoints,reference)
    stages = list(table('stages')); reference_stages = list(table('stages',True))
    preparation = preparation_differences(stages,reference_stages)
    tolerance = public['time_tolerance_ns']
    selected = select_call(changes,public['anchor_rank'],tolerance)
    endpoint_evidence = [dict(rank_id=key[0],local_record_id=key[1],preparation_delta_ns=value)
                         for key,value in sorted(preparation.items()) if value > tolerance]
    counters = window_differences(table('ports'),table('ports',True)) if manifest['condition']!='E1' else {}
    enhanced = manifest['condition'] in {'E4','E5','E5S'}
    queues = window_differences(table('queues'),table('queues',True),queue=True) if enhanced else {}
    interval = temporal_scope(selected,endpoints,reference,stages,reference_stages)
    mapped = manifest['condition'] in {'E3','E5','E3S','E5S'}
    linked = (related_windows(table('relations'),selected) | related_windows(table('relations',True),selected)) if mapped else set()

    def included(key):
        if selected is None:
            return False
        if mapped:
            return key in linked
        return key[2] < interval[1] and key[3] > interval[0]

    whole = defaultdict(float); scoped_bytes = defaultdict(float); scoped_queue = defaultdict(float)
    window_counts = defaultdict(int)
    for key,value in counters.items():
        whole[key[:2]] += value
        if included(key):
            scoped_bytes[key[:2]] += value; window_counts[key[:2]] += 1
    for key,value in queues.items():
        if included(key):
            scoped_queue[key[:2]] += value
    selected_ports = set(scoped_bytes) | set(scoped_queue)
    candidates = [dict(switch_id=p[0],port_id=p[1],traffic_difference_bytes=whole[p]) for p in sorted(whole)]
    scoped = [dict(switch_id=p[0],port_id=p[1],traffic_difference_bytes=scoped_bytes.get(p,0),
        queue_excess_area_byte_ns=scoped_queue.get(p) if enhanced and list(p) in public['queue_ports'] else None,
        associated_changed_windows=window_counts.get(p,0)) for p in selected_ports]
    scoped.sort(key=lambda r:(-(r['queue_excess_area_byte_ns'] or 0),-r['traffic_difference_bytes'],r['switch_id'],r['port_id']))
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
        excluded_from_selected_call=[r for r in candidates if port_key(r) not in selected_ports],
        association_rule='ideal_binary_windows' if mapped else 'public_local_call_order_time_envelope',
        selection_rule='first_public_call_ordinal_with_any_rank_duration_or_completion_increase',
        inferred_time_envelope_ns=interval,
        selected_related_windows=len(linked) if mapped else None,
        candidate_scope='selected_logical_call_business_or_declared_state_relations_not_all_possible_root_causes',
        queue_coverage=public['queue_ports'] if manifest['condition'] in {'E4','E5','E5S'} else [],
        endpoint_differences=changes,completion_proxy_delta_ns=proxy_delta,
        material_round_prediction=proxy_delta>threshold,
        qualification='Endpoint completion proxy; true round delta is measured independently by evaluation. '
                      'Traffic/queue changes alone do not establish causal root location.')
    save(output,value)
    return dict(passed=True,output=str(output),assessment=assessment,port_candidates=len(scoped))
