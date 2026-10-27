"""Evaluate prefix-only bounds against separated future truth, never fit them."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from experiments.admission_bounds.bounds import require
from experiments.admission_bounds.predict import load
from experiments.admission_bounds.prepare import save


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if start >= end:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def length(intervals):
    return sum(b-a for a, b in union(intervals))


def overlap(a, b):
    return length((max(s, u), min(t, v)) for s, t in union(a) for u, v in union(b))


def subtract(intervals, occupied):
    result = []
    for start, end in union(intervals):
        cursor = start
        for a, b in union(occupied):
            if a > cursor:
                result.append((cursor, min(a, end)))
            cursor = max(cursor, b) if a <= end else end
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return union(result)


def quantile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, int((len(ordered)-1)*fraction))] if ordered else None


def ip(gpu, plane):
    return 0x0b000001 + 32*gpu + plane


def condition(stage, name):
    predictions = [json.loads(l) for l in (stage/'predictions'/(name+'.jsonl')).read_text().splitlines()]
    control = [json.loads(l) for l in (stage/'truth'/name/'control.jsonl').read_text().splitlines()]
    result = load(stage/'truth'/name/'result.json')
    program = load(stage/'observed/program.json')
    require(result['passed'] and result['drained'] and result['packet_capture']['complete'] and
            result['packet_capture']['omitted_events'] == 0, 'incomplete network evidence')
    connections = {c['id']: c for c in program['connections']}
    work = {w['id']: w for w in program['operations']}
    native, commits, callbacks, registration = [], [], {}, {}
    for row in control:
        message = row['message']
        if row['direction'] == 'native':
            native.append(message)
            for batch in message['batches']:
                for event in batch['events']:
                    key = (event['operation'], event['event'])
                    require(key not in callbacks and event['time_ns'] <= batch['now_ns'], 'duplicate/future callback truth')
                    callbacks[key] = event['time_ns']
        else:
            require(row['direction'] == 'frontend', 'unknown control direction')
            commits.append(message)
            for op in message['submissions']:
                require(op == work[op['id']] and op['id'] not in registration, 'work identity/registration mismatch')
                registration[op['id']] = message['now_ns']
    require(len(predictions) == len(native) == len(commits) and set(registration) == work.keys(), 'decision/work count mismatch')
    require([json.loads(l) for l in (stage/'observed'/name/'decisions.jsonl').read_text().splitlines()] == native,
            'observed decision prefix changed')
    submitted, seen, checks = set(), set(), defaultdict(int)
    intervals = defaultdict(list)
    gaps, horizons = defaultdict(list), defaultdict(list)
    provider = result['transactions']['transport']['work']
    provider_intervals = defaultdict(list)
    for item in provider:
        connection = connections[item['stream']//2]
        require(connection['src'] != connection['dst'] and 0 <= item['published_ns'] <= item['accepted_ns'] <= item['acked_ns'],
                'invalid transport lifetime')
        provider_intervals['plane:'+str(connection['plane'])].append((item['published_ns'], item['acked_ns']))
    quiet_false = []
    quiet_checks = 0
    for row, decision, commit in zip(predictions, native, commits):
        now = row['now_ns']
        require(row['decision_id'] == decision['decision_id'] == commit['decision_id'] and
                now == decision['now_ns'] == commit['now_ns'], 'decision identity mismatch')
        require(row['submitted_now'] == [op['id'] for op in commit['submissions']], 'prefix replay changed submission')
        submitted.update(row['submitted_now'])
        for batch in decision['batches']:
            seen.update((e['operation'], e['event']) for e in batch['events'])
        pending_work = work.keys()-submitted
        expected_callback_keys = callbacks.keys()-seen
        previous = None
        for mode in ('causal', 'propagation', 'declared'):
            predicted = row['modes'][mode]
            work_bound = dict(predicted['work'])
            callback_bound = {(i, e): t for i, e, t in predicted['callbacks']}
            require(work_bound.keys() == pending_work and callback_bound.keys() == expected_callback_keys,
                    'pending work/callback coverage mismatch')
            for identity, bound in work_bound.items():
                require(type(bound) is int and now <= bound <= registration[identity], 'nonconservative work bound')
                checks['work_bounds'] += 1
            for key, bound in callback_bound.items():
                require(type(bound) is int and now <= bound <= callbacks[key], 'nonconservative callback bound')
                checks['callback_bounds'] += 1
            if previous:
                require(all(previous[0][i] <= t for i, t in work_bound.items()) and
                        all(previous[1][k] <= t for k, t in callback_bound.items()), 'nonmonotone cost refinement')
            previous = work_bound, callback_bound
            for scope, bound in predicted['next'].items():
                identities = []
                for identity in pending_work:
                    op = work[identity]
                    wire = op['kind'] == 'remote' and connections[op['connection']]['src'] != connections[op['connection']]['dst']
                    if scope == 'all_work' or (wire and (scope == 'wire' or scope == 'plane:'+str(connections[op['connection']]['plane']))):
                        identities.append(identity)
                expected = min((work_bound[i] for i in identities), default=None)
                require(bound == expected, 'scope minimum mismatch')
                if bound is not None:
                    actual = min(registration[i] for i in identities)
                    require(bound <= actual, 'nonconservative scope bound')
                    gaps[mode, scope].append(actual-bound)
                    horizons[mode, scope].append(bound-now)
                    intervals[mode, scope].append((now, bound))
        for plane, pending in row['completed_wire_pending_by_plane'].items():
            domain_ops = {i for i in submitted if work[i]['kind'] == 'remote' and
                connections[work[i]['connection']]['src'] != connections[work[i]['connection']]['dst'] and
                str(connections[work[i]['connection']]['plane']) == plane}
            expected = domain_ops - {i for i, e in seen if e == 'completed'}
            require(set(pending) == expected, 'application quiet predicate mismatch')
            if domain_ops and not pending:
                quiet_checks += 1
                live = [w for w in provider if str(connections[w['stream']//2]['plane']) == plane and
                        w['published_ns'] <= now < w['acked_ns']]
                if live:
                    quiet_false.append(dict(decision_id=row['decision_id'], time_ns=now, plane=int(plane),
                        live_transport_work=len(live), latest_ack_ns=max(w['acked_ns'] for w in live),
                        response_streams=sum(w['stream'] % 2 for w in live)))
    # Verify response work independently against application sequence and reply receipt.
    operations = result['transactions']['operations']
    # Evaluation-only full timeline: callbacks hidden in a batch must not be
    # misrepresented as observations available at an earlier physical DECIDE.
    timeline_quiet = {}
    for scope, occupied in provider_intervals.items():
        plane = int(scope.split(':')[1])
        domain = [op for op in operations if connections[op['connection']]['src'] != connections[op['connection']]['dst'] and
                  connections[op['connection']]['plane'] == plane]
        boundaries = sorted({result['finish_ns'], *(registration[op['id']] for op in domain),
                             *(op['completed_ns'] for op in domain)})
        quiet = [(a, b) for a, b in zip(boundaries, boundaries[1:]) if
                 any(registration[op['id']] <= a for op in domain) and
                 not any(registration[op['id']] <= a < op['completed_ns'] for op in domain)]
        intersections = union((max(a, c), min(b, d)) for a, b in quiet for c, d in occupied)
        timeline_quiet[scope] = dict(quiet_interval_ns=length(quiet), live_transport_overlap_ns=length(intersections),
            live_overlap_examples=intersections[:4], future_truth_only_not_an_earlier_frontend_observation=True)
    reply_tails = []
    for connection in connections.values():
        if connection['src'] == connection['dst']:
            continue
        atomic = sorted((op for op in operations if op['connection'] == connection['id'] and op['verb'] == 'fetch_add'),
                        key=lambda op: op['sequence'])
        replies = sorted((w for w in provider if w['stream'] == 2*connection['id']+1), key=lambda w: w['begin'])
        require(len(atomic) == len(replies), 'atomic reply work count mismatch')
        for op, reply in zip(atomic, replies):
            require(reply['bytes'] == 40 and reply['published_ns'] == op['applied_ns'] and
                    reply['accepted_ns'] == op['response_ns'] and
                    op['completed_ns'] >= max(op['request_acked_ns'], op['response_ns']), 'reply/application identity mismatch')
            if reply['acked_ns'] > op['completed_ns']:
                reply_tails.append(dict(operation=op['id'], connection=connection['id'], stream=reply['stream'],
                    completed_ns=op['completed_ns'], response_ns=op['response_ns'],
                    response_acked_ns=reply['acked_ns'], tail_ns=reply['acked_ns']-op['completed_ns']))
    # Only physical TX events are counted; potential windows are not skip certificates.
    capture = result['packet_capture']
    require(len(capture['events']) == len(capture['wire_headers']['event_header_indices']), 'capture/header mismatch')
    tuples = {}
    for c in connections.values():
        if c['src'] == c['dst']:
            continue
        for src, dst, port in ((c['src'], c['dst'], c['request_port']), (c['dst'], c['src'], c['response_port'])):
            tuples[ip(src, c['plane']), ip(dst, c['plane']), port, port] = c
    transmissions = defaultdict(list)
    physical_acks_after_completion = 0
    for event, index in zip(capture['events'], capture['wire_headers']['event_header_indices']):
        if event[3] != 0:
            continue
        h = capture['wire_headers']['headers'][index]
        key = (h[1], h[2], h[4], h[5])
        if event[6] == 252:
            key = key[1], key[0], key[3], key[2]
        require(event[6] in (17, 252) and key in tuples, 'unsupported/unmapped physical transmission')
        c = tuples[key]
        transmissions['plane:'+str(c['plane'])].append(event[0])
        if event[6] == 252 and event[0] > max(op['completed_ns'] for op in operations if op['connection'] == c['id']):
            physical_acks_after_completion += 1
    statistics = {}
    for mode in ('causal', 'propagation', 'declared'):
        statistics[mode] = {}
        for scope in ('all_work', 'wire', *sorted(provider_intervals)):
            windows = union(intervals[mode, scope])
            stats = dict(finite_boundaries=len(horizons[mode, scope]),
                positive_boundaries=sum(t > 0 for t in horizons[mode, scope]),
                maximum_horizon_ns=max(horizons[mode, scope], default=0),
                p50_horizon_ns=quantile(horizons[mode, scope], .5),
                p50_slack_to_actual_ns=quantile(gaps[mode, scope], .5),
                union_window_ns=length(windows))
            if scope.startswith('plane:'):
                covered = overlap(windows, provider_intervals[scope])
                uncovered = subtract(windows, provider_intervals[scope])
                require(length(uncovered) == length(windows)-covered, 'interval subtraction mismatch')
                stats.update(transport_live_overlap_ns=covered,
                    not_covered_by_saved_transport_work_ns=length(windows)-covered,
                    physical_transmissions_in_uncovered_windows=sum(any(a <= t < b for a, b in uncovered) for t in transmissions[scope]),
                    physical_transmissions_in_windows=sum(any(a <= t < b for a, b in windows) for t in transmissions[scope]),
                    certified_packet_skip_ns=0)
            statistics[mode][scope] = stats
    return dict(decisions=len(predictions), work=len(work), callbacks=len(callbacks), checks=dict(checks),
        timing_bounds=statistics, naive_application_quiet_checks=quiet_checks,
        naive_application_quiet_false=quiet_false,
        evaluation_only_application_quiet_timeline=timeline_quiet,
        atomic_reply_tail_count=len(reply_tails), maximum_atomic_reply_tail_ns=max((w['tail_ns'] for w in reply_tails), default=0),
        reply_tail_examples=reply_tails[:4], physical_ACK_transmissions_after_connection_operations_complete=physical_acks_after_completion,
        finished_ns=result['finish_ns'], packet_skip_safety_proven=False, speedup_measured=False,
        hardware_calibrated=False)


def evaluate(stage, output):
    source = load(stage/'source.json')
    for name, digest in source['files'].items():
        require(hashlib.sha256((stage/name).read_bytes()).hexdigest() == digest, 'evidence identity changed')
    values = {name: condition(stage, name) for name in ('baseline', 'bandwidth')}
    output.mkdir(parents=True, exist_ok=False)
    save(output/'summary.json', values)
    return values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    values = evaluate(args.stage.resolve(), args.output.resolve())
    print(json.dumps({name: dict(checks=v['checks'], quiet_false=len(v['naive_application_quiet_false']),
        atomic_reply_tails=v['atomic_reply_tail_count']) for name, v in values.items()}))


if __name__ == '__main__':
    main()
