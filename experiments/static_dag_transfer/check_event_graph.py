"""Small independent witnesses for event gating, not a platform fixture."""
from copy import deepcopy
import json

from experiments.static_dag_transfer.event_graph import EventGraph


def reject(function):
    try:
        function()
    except (ValueError, KeyError):
        return
    raise AssertionError('invalid case accepted')


def main():
    program = dict(operations=[dict(id=i, kind='local', after=[]) for i in range(3)])
    graph = dict(schema_version='moe-generic-event-dag/1', events=[
        dict(id='a', deps=[], actions=[dict(work=0, wait_before=[])], callback=[0, 'local_started']),
        dict(id='b', deps=['a'], actions=[dict(work=1, wait_before=[]),
             dict(work=2, wait_before=[[1, 'completed']])], callback=[2, 'completed'])])

    def decision(time, callbacks):
        row = dict(now_ns=time, events=[dict(operation=i, event=e, time_ns=time) for i,e in callbacks], drained=False)
        return dict(now_ns=time, batches=[row])

    model = EventGraph(graph, program)
    assert [n['id'] for n in model.advance(decision(0, []))] == [0]
    assert [n['id'] for n in model.advance(decision(1, [(0, 'local_admitted')]))] == []
    assert model.wake_groups() == [[dict(operation=0, event='local_started')]]
    assert [n['id'] for n in model.advance(decision(2, [(0, 'local_started')]))] == [1]
    assert model.wake_groups() == [[dict(operation=1, event='completed')]]
    assert [n['id'] for n in model.advance(decision(3, [(1, 'local_admitted'), (1, 'local_started')]))] == []
    assert [n['id'] for n in model.advance(decision(4, [(1, 'completed')]))] == [2]
    model.advance(decision(5, [(2, 'local_admitted'), (2, 'local_started'), (2, 'completed')]))
    assert model.snapshot()['all_events_fired']
    reject(lambda: model.advance(decision(6, [(2, 'completed')])))
    cyclic = deepcopy(graph)
    cyclic['events'][0]['deps'] = ['b']
    reject(lambda: EventGraph(cyclic, program))
    unknown = deepcopy(graph)
    unknown['events'][0]['callback'] = [99, 'local_started']
    reject(lambda: EventGraph(unknown, program))
    history = EventGraph(graph, program)
    history.advance(decision(0, []))
    old = decision(2, [(0, 'local_started')])['batches'][0]
    reject(lambda: history.advance(dict(now_ns=3, batches=[old, decision(3, [])['batches'][0]])))
    print(json.dumps(dict(passed=True, checks=['start_not_admission_or_completion', 'shared_preparation_chain',
        'actual_callback_frontiers', 'duplicate_callback_rejected', 'cycle_rejected',
        'unknown_callback_rejected', 'suppressed_productive_batch_rejected'], new_networks=0)))


if __name__ == '__main__':
    main()
