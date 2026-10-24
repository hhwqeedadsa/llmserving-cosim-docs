"""Replay saved callbacks only. Never starts a network or fabricates an event."""
import argparse
import hashlib
import json
from pathlib import Path

from moe_ub_cosim.modern_native import ModernNativeController
from moe_ub_cosim.modern_native_wakeups import conditions, satisfied


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def pairs(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) % 2 == 0, 'saved transcript has an unmatched control message'
    result = []
    for index in range(0, len(rows), 2):
        incoming, outgoing = rows[index:index+2]
        assert incoming['direction'] == 'native' and outgoing['direction'] == 'frontend'
        decision, command = incoming['message'], outgoing['message']
        assert decision['decision_id'] == command['decision_id'] == index//2
        assert decision['now_ns'] == command['now_ns']
        result.append((decision, command))
    return result


def replay(directory, goal=None):
    controller = ModernNativeController(*(load(directory/name) for name in
                                        ('topology.json', 'protocol.json', 'input.json')))
    transcript = pairs(directory/'control.jsonl')
    pending, occurred, subscriptions, wakes = [], set(), [], []
    log, maximum_groups, maximum_terms = [], 0, 0
    divergence = None
    for decision, command in transcript:
        pending.extend(decision['events'])
        occurred.update((row['operation'], row['event']) for row in decision['events'])
        wake = goal is None or not wakes or decision['drained'] or satisfied(subscriptions, occurred)
        if not wake:
            if command['submissions'] or command['finish']:
                divergence = dict(kind='missed_submission_or_finish', original_decision=decision['decision_id'],
                                  time_ns=decision['now_ns'], expected=command['submissions'])
                break
            continue
        event = dict(decision, decision_id=len(wakes), events=pending)
        nodes = controller.advance(event)
        if nodes != command['submissions']:
            divergence = dict(kind='submission_batch_content_or_order_changed',
                              original_decision=decision['decision_id'], time_ns=decision['now_ns'],
                              expected=command['submissions'], actual=nodes)
            break
        finished = not nodes and event['drained'] and controller.runtime.snapshot()['all_events_fired']
        assert finished == command['finish'], 'finish differs from original control record'
        wakes.append(decision['decision_id'])
        log.append(dict(original_decision=decision['decision_id'], now_ns=decision['now_ns'],
                        delivered_callbacks=len(pending), native_submissions=[node['id'] for node in nodes]))
        pending = []
        if goal is not None:
            subscriptions = conditions(controller, goal=goal)
            maximum_groups = max(maximum_groups, len(subscriptions))
            maximum_terms = max(maximum_terms, sum(map(len, subscriptions)))
            log[-1]['next_conditions'] = subscriptions
    expected = load(directory/'modern-execution.json')
    actual = controller.snapshot()
    if goal is None:
        assert actual == expected, 'unmodified controller no longer reproduces saved full snapshot'
    if goal == 'events':
        assert divergence is None, 'event-driven wakeup changed a native submission'
        assert actual['events'] == expected['events'], 'event-driven wakeup changed modern event log'
        assert actual['native_operations'] == expected['native_operations'], 'native nodes changed'
        assert actual['native_tasks'] == expected['native_tasks'], 'native task mapping changed'
    old = {event['event']: event for event in expected['events']}
    new = {event['event']: event for event in actual['events']}
    delays = {name: event['time_ns']-old[name]['time_ns'] for name, event in new.items()}
    assert all(delay >= 0 for delay in delays.values()), 'candidate fires event before original execution'
    result = dict(goal=goal or 'original', offline_only=True, network_executed=False,
                  original_decisions=len(transcript), candidate_decisions=len(wakes), skipped=len(transcript)-len(wakes),
                  divergence=divergence, exact_native_submission_prefix=divergence is None,
                  native_nodes_retained=actual['native_operations'] == expected['native_operations'],
                  native_nodes_sha256=value_sha(actual['native_operations']),
                  modern_event_log_sha256=value_sha(actual['events']),
                  native_task_mapping_sha256=value_sha(actual['native_tasks']),
                  complete_original_record=expected['runtime']['all_events_fired'],
                  modern_events_original=len(old), modern_events_processed=len(new),
                  modern_event_log_equal=actual['events'] == expected['events'],
                  processed_event_order_equal=list(new) == [name for name in old if name in new],
                  delayed_bookkeeping_events=sum(delay > 0 for delay in delays.values()),
                  maximum_bookkeeping_delay_ns=max(delays.values(), default=0),
                  undelivered_callbacks=len(pending), max_condition_groups=maximum_groups,
                  max_total_condition_terms=maximum_terms, all_events_fired=actual['runtime']['all_events_fired'],
                  same_ns_scheduler_order_verified=False, four_request_completion_proven=False,
                  wake_original_decisions=wakes, deliveries=log)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    report = dict(schema_version='moe-dependency-wakeup-offline/1', network_executed=False,
                  native_limit_unchanged=256, cases={})
    for name in ('synthetic-healthy', 'synthetic-fault', 'four-routes'):
        directory = args.source/name
        identities = {file: sha(directory/file) for file in
                      ('topology.json', 'protocol.json', 'input.json', 'control.jsonl', 'modern-execution.json')}
        modes = {goal or 'original': replay(directory, goal) for goal in (None, 'events', 'submissions')}
        report['cases'][name] = dict(source_sha256=identities, modes=modes)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({name: {mode: {key: result[key] for key in
        ('candidate_decisions', 'exact_native_submission_prefix', 'modern_event_log_equal',
         'delayed_bookkeeping_events', 'maximum_bookkeeping_delay_ns', 'all_events_fired')}
        for mode, result in case['modes'].items()} for name, case in report['cases'].items()}, indent=2))


if __name__ == '__main__':
    main()
