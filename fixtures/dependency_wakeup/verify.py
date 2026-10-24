"""Independent selection/order oracle; imports no controller or wakeup code."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def value_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify(report, source):
    assert report['network_executed'] is False and report['native_limit_unchanged'] == 256
    summary = {}
    assert set(report['cases']) == {'synthetic-healthy', 'synthetic-fault', 'four-routes'}
    for name, case in report['cases'].items():
        directory = source/name
        for file, expected in case['source_sha256'].items():
            assert hashlib.sha256((directory/file).read_bytes()).hexdigest() == expected, 'source hash mismatch'
        rows = [json.loads(line) for line in (directory/'control.jsonl').read_text().splitlines()]
        original = load(directory/'modern-execution.json')
        candidate = case['modes']['events']
        assert candidate['offline_only'] and candidate['network_executed'] is False
        assert candidate['exact_native_submission_prefix'] and candidate['native_nodes_retained']
        assert candidate['divergence'] is None and candidate['modern_event_log_equal']
        assert candidate['modern_event_log_sha256'] == value_sha(original['events']), 'modern event log changed'
        assert candidate['native_nodes_sha256'] == value_sha(original['native_operations']), 'native nodes changed'
        assert candidate['native_task_mapping_sha256'] == value_sha(original['native_tasks']), 'task identity changed'
        assert candidate['all_events_fired'] == candidate['complete_original_record'] == original['runtime']['all_events_fired'], 'false completion'
        assert candidate['modern_events_original'] == candidate['modern_events_processed'] == len(original['events'])
        assert candidate['delayed_bookkeeping_events'] == candidate['maximum_bookkeeping_delay_ns'] == 0
        assert candidate['same_ns_scheduler_order_verified'] is False and candidate['four_request_completion_proven'] is False
        deliveries = {item['original_decision']: item for item in candidate['deliveries']}
        assert len(deliveries) == len(candidate['deliveries'])
        assert list(deliveries) == candidate['wake_original_decisions']
        seen, registered, pending, subscriptions = set(), {}, [], []
        callbacks, previous_ns, previous_drained = [], -1, False
        wakes = []
        for index in range(0, len(rows), 2):
            incoming, outgoing = rows[index:index+2]
            assert incoming['direction'] == 'native' and outgoing['direction'] == 'frontend'
            event, command = incoming['message'], outgoing['message']
            identity = index//2
            assert event['decision_id'] == command['decision_id'] == identity
            assert previous_ns <= event['now_ns'] == command['now_ns']
            previous_ns = event['now_ns']
            for callback in event['events']:
                key = callback['operation'], callback['event']
                assert key not in seen and key[0] in registered
                assert callback['time_ns'] <= event['now_ns']
                seen.add(key)
                pending.append(callback)
                callbacks.append(callback)
            triggered = any(all((term['operation'], term['event']) in seen for term in group) for group in subscriptions)
            # Existing native policy wakes on a transition to drained, not on
            # every arbitrary snapshot already known drained.
            expected_wake = identity == 0 or (event['drained'] and not previous_drained) or triggered
            previous_drained = event['drained']
            assert (identity in deliveries) == expected_wake, 'wake does not match declared actual callbacks'
            if identity not in deliveries:
                assert not command['submissions'] and not command['finish'], 'missed submission or finish'
                continue
            delivery = deliveries[identity]
            assert delivery['now_ns'] == event['now_ns'], 'submission time changed'
            assert delivery['delivered_callbacks'] == len(pending), 'callback history lost'
            assert delivery['native_submissions'] == [node['id'] for node in command['submissions']], 'batch/order changed'
            for node in command['submissions']:
                assert node['id'] not in registered
                registered[node['id']] = node
            pending = []
            subscriptions = delivery['next_conditions']
            assert isinstance(subscriptions, list) and len(subscriptions) <= len(registered)
            groups = []
            for group in subscriptions:
                # Accepted candidate wakes on one next modern event: its sole
                # missing intrinsic callback, with application parents done.
                assert len(group) == 1, 'event policy must not wait for unrelated callbacks'
                term, = group
                assert set(term) == {'operation', 'event'}
                assert type(term['operation']) is int and term['operation'] in registered, 'future/unsubmitted condition'
                node = registered[term['operation']]
                allowed = {'local_started', 'completed'} if node['kind'] == 'local' else {'submitted', 'applied', 'completed'}
                assert term['event'] in allowed
                key = term['operation'], term['event']
                assert key not in seen, 'condition already delivered'
                groups.append(key)
            assert len(set(groups)) == len(groups)
            wakes.append(identity)
        assert candidate['undelivered_callbacks'] == len(pending)
        assert candidate['original_decisions'] == len(rows)//2
        assert candidate['candidate_decisions'] == len(wakes) == len(deliveries)
        assert candidate['skipped'] == len(rows)//2-len(wakes)
        assert list(registered.values()) == original['native_operations']
        assert len(callbacks) == len(seen)
        summary[name] = dict(original_decisions=len(rows)//2, event_wakeups=len(wakes),
                             native_operations=len(registered), callbacks_retained=len(callbacks),
                             complete_original_record=original['runtime']['all_events_fired'])
    return dict(verified=True, network_executed=False, same_ns_scheduler_order_verified=False,
                four_request_completion_proven=False, cases=summary)


def reject_tampering(report, source):
    edits = {
        'false_completion': lambda r: r['cases']['four-routes']['modes']['events'].update(all_events_fired=True),
        'changed_event_log': lambda r: r['cases']['synthetic-healthy']['modes']['events'].update(modern_event_log_sha256='0'*64),
        'lost_callbacks': lambda r: r['cases']['synthetic-healthy']['modes']['events']['deliveries'][1].update(delivered_callbacks=0),
        'wrong_submission_time': lambda r: r['cases']['synthetic-healthy']['modes']['events']['deliveries'][0].update(now_ns=1),
        'unsubmitted_condition': lambda r: r['cases']['synthetic-healthy']['modes']['events']['deliveries'][0]['next_conditions'][0][0].update(operation=65535),
        'batch_order': lambda r: r['cases']['synthetic-healthy']['modes']['events']['deliveries'][0]['native_submissions'].reverse(),
    }
    rejected = []
    for name, edit in edits.items():
        changed = deepcopy(report)
        edit(changed)
        assert changed != report, 'tamper edit did not change input'
        try:
            verify(changed, source)
        except AssertionError:
            rejected.append(name)
        else:
            raise AssertionError('tamper accepted: '+name)
    return rejected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    report = load(args.report)
    result = verify(report, args.source)
    result['tamper_rejections'] = reject_tampering(report, args.source)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
