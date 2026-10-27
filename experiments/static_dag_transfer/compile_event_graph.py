"""Convert BASELINE public plan/bindings to a frozen, untimed generic graph."""
import argparse
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from experiments.static_dag_transfer.event_graph import EventGraph


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def compile_graph(reference):
    plan = load(reference/'modern-plan.json')
    native = load(reference/'modern-execution.json')
    observed = {e['event']: e for e in native['events']}
    events = {e['id']: dict(id=e['id'], deps=list(e['deps']), actions=[], callback=None) for e in plan['events']}
    ops = {o['id']: o for o in plan['operations']}
    preparations = {(t['call'], t['rank']): int(i) for i, t in native['native_tasks'].items() if t['role'] == 'count_prepare'}
    for i, task in native['native_tasks'].items():
        identity = int(i)
        if task['role'] == 'count_prepare':
            continue
        event = events[task['event']]
        if task['role'] == 'operation':
            op = ops[task['modern_operation']]
            if op['kind'] in ('rank_count', 'expert_count_block'):
                prep = preparations[op['call'], op['src']]
                event['actions'].append(dict(work=prep, wait_before=[]))
                event['actions'].append(dict(work=identity, wait_before=[[prep, 'completed']]))
            else:
                event['actions'].append(dict(work=identity, wait_before=[]))
        else:
            assert task['role'] == 'event'
            event['actions'].append(dict(work=identity, wait_before=[]))
    for name, event in events.items():
        row = observed[name]
        if row['origin'] == 'native_callback':
            event['callback'] = [row['native_operation'], row['native_event']]

    # Runtime adds a warp-sync boundary after all per-token slot atomics.
    slots = defaultdict(list)
    for slot in plan['slots']:
        slots[slot['call'], slot['src'], slot['token']].append(slot['id'])
    sync_edges = 0
    for op in ops.values():
        if op['phase'] == 'dispatch' and op['kind'] == 'token_data':
            event = events[op['events']['issued']]
            for parent in slots[op['call'], op['src'], op['token']]:
                if parent not in event['deps']:
                    event['deps'].append(parent)
                    sync_edges += 1

    # Only baseline bound owner/channel/index choices; never evaluation times.
    channels = defaultdict(list)
    for row in native['runtime']['log']:
        if row['kind'] != 'issued' or 'receive_index' not in row:
            continue
        op = ops[row['operation']]
        binding = row['binding']
        channels[op['call'], op['src'], binding['sm'], binding['rotated_warp']].append((row['receive_index'], op))
    channel_edges = 0
    for rows in channels.values():
        rows.sort(key=lambda item: item[0])
        for (_, previous), (_, current) in zip(rows, rows[1:]):
            reduced, = events[current['events']['issued']]['deps']
            parent = previous['events']['issued']
            if parent not in events[reduced]['deps']:
                events[reduced]['deps'].append(parent)
                channel_edges += 1
    for event in events.values():
        event['deps'].sort()
    return dict(schema_version='moe-generic-event-dag/1', events=list(events.values()),
                application_events=[e['id'] for e in plan['events'] if e['kind'] == 'application_stage_complete'],
                metadata=dict(extraction_network='baseline_only', baseline_dynamic_bindings_frozen=True,
                              reference_timestamps_embedded=False, dispatch_sync_edges_added=sync_edges,
                              combine_channel_edges_added=channel_edges))


def replay(reference, graph, program):
    controller = EventGraph(graph, program)
    transcript = [json.loads(line) for line in (reference/'control.jsonl').read_text().splitlines()]
    for index in range(0, len(transcript), 2):
        incoming, outgoing = transcript[index:index+2]
        assert incoming['direction'] == 'native' and outgoing['direction'] == 'frontend'
        nodes = controller.advance(incoming['message'])
        assert nodes == outgoing['message']['submissions'], f'submission mismatch at decision {index//2}'
        controller.wake_groups()
    expected = load(reference/'modern-execution.json')['events']
    fields = ('event', 'time_ns', 'native_operation', 'native_event', 'native_time_ns')
    assert controller.log == [{k: row[k] for k in fields if k in row} for row in expected]
    assert controller.snapshot()['all_events_fired']
    return dict(passed=True, native_nodes=len(controller.work), events=len(controller.events),
                replayed_decisions=controller.decisions, logical_steps=controller.steps,
                exact_submissions=True, exact_event_times_and_order=True, new_networks=0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    graph = compile_graph(args.stage/'reference/baseline')
    program = load(args.stage/'program.json')
    save(args.stage/'event-graph.json', graph)
    verification = replay(args.stage/'reference/baseline', graph, program)
    save(args.stage/'offline-equivalence.json', verification)
    ledger = load(args.stage/'execution.json')
    ledger['event_graph_sha256'] = hashlib.sha256((args.stage/'event-graph.json').read_bytes()).hexdigest()
    save(args.stage/'execution.json', ledger)
    print(json.dumps(verification))


if __name__ == '__main__':
    main()
