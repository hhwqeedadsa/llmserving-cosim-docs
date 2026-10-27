"""Explain strict transfer mismatch without modifying inputs or rerunning."""
import argparse
from copy import deepcopy
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def semantic_identity(task):
    # These are stable task descriptors from the independently recorded plan.
    # No timing/packet matching or fitted correspondence is used.
    if task['role'] == 'count_prepare':
        return 'count_prepare', task['call'], task['rank']
    return task['role'], task['event']


def replace_operation_keys(value, mapping):
    if isinstance(value, list):
        return [replace_operation_keys(x, mapping) for x in value]
    if isinstance(value, dict):
        return {k: mapping[v] if k == 'operation' and type(v) is int else replace_operation_keys(v, mapping)
                for k,v in value.items()}
    return value


def diagnose(stage):
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['network_executions'] == 2
    assert [r['name'] for r in ledger['records']] == ['baseline', 'bandwidth']
    assert not (stage/'propagation').exists()
    strict = load(stage/'bandwidth-equivalence.json')
    assert not strict['equivalent']
    baseline = load(stage/'reference/baseline/modern-execution.json')
    detailed = load(stage/'reference/bandwidth/modern-execution.json')
    base_tasks = baseline['native_tasks']
    lookup = {semantic_identity(t): int(i) for i,t in base_tasks.items()}
    assert len(lookup) == len(base_tasks)
    mapping = {int(i):lookup[semantic_identity(t)] for i,t in detailed['native_tasks'].items()}
    assert set(mapping.values()) == set(map(int, base_tasks))
    assert all(detailed['native_tasks'][str(i)] == base_tasks[str(j)] for i,j in mapping.items())
    old = load(stage/'reference/bandwidth/result.json')
    new = load(stage/'bandwidth/result.json')
    actual = load(stage/'bandwidth/event-execution.json')
    def remap_work(rows):
        result = replace_operation_keys(rows, mapping)
        for op in result:
            op['id'] = mapping[op['id']]
        return sorted(result, key=lambda x:x['id'])
    fields = ('event', 'time_ns', 'native_operation', 'native_event', 'native_time_ns')
    logs = [{k:e[k] for k in fields if k in e} for e in detailed['events']]
    for e in logs:
        if 'native_operation' in e:
            e['native_operation'] = mapping[e['native_operation']]
    transactions = replace_operation_keys(old['transactions'], mapping)
    transactions['operations'] = remap_work(old['transactions']['operations'])
    def registration(rows):
        return [dict(operation=mapping[r['operation']], time_ns=r['time_ns']) for r in rows]
    def commits(path, remap):
        result = []
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row['direction'] != 'frontend':
                continue
            msg = row['message']
            if not msg['submissions'] and not msg['finish']:
                continue
            nodes = deepcopy(msg['submissions'])
            if remap:
                nodes = replace_operation_keys(nodes, mapping)
                for node in nodes:
                    node['id'] = mapping[node['id']]
            result.append(dict(time_ns=msg['now_ns'], nodes=nodes, finish=msg['finish']))
        return result
    normalized_native = remap_work(detailed['native_operations'])
    checks = dict(
        graph_work_and_after=normalized_native == baseline['native_operations'],
        all_transactions_and_final_memory=transactions == new['transactions'],
        all_local_work=remap_work(old['local_work']) == sorted(new['local_work'], key=lambda x:x['id']),
        application_event_order_times_and_callbacks=logs == actual['events'],
        native_callback_order_and_times=replace_operation_keys(old['session']['events'], mapping) == new['session']['events'],
        productive_commits=commits(stage/'reference/bandwidth/control.jsonl', True) == commits(stage/'bandwidth/control.jsonl', False),
        native_registration_order_and_time=registration(old['session']['registrations']) == [dict(operation=r['operation'],time_ns=r['time_ns']) for r in new['session']['registrations']],
        all_physical_packets=old['packet_capture'] == new['packet_capture'],
        all_queue_events=old['queue_events'] == new['queue_events'],
        drain_time=old['finish_ns'] == new['finish_ns'])
    assert all(checks.values()), checks
    return dict(passed=True, outcome='task_ids_renamed_but_observable_execution_equal',
        strict_gate_preserved=True, further_networks_executed=0, three_network_ranking_evaluated=False,
        renamed_task_ids=sum(i!=j for i,j in mapping.items()), mapping={str(i):j for i,j in mapping.items() if i!=j},
        identity_rule='role/event; count_prepare uses call/rank; no time or packet fitting',
        checks=checks, application_finish_ns=strict['application_finish_ns'],
        all_events_and_source_release_ns=strict['all_events_and_source_release_ns'],
        drain_ns=strict['drain_ns'], actual_new_networks=2, hardware_calibrated=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(args.stage)
    save(args.stage/'transfer-diagnosis.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
