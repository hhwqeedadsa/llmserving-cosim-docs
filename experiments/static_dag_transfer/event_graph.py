"""Generic event-DAG executor; knows callbacks/work chains, not MoE semantics.

The graph is a baseline-specific captured input, never a timing trace: it has
no recorded event times. Only actual callbacks advance this executor.
"""
from copy import deepcopy
import json


def require(condition, message):
    if not condition:
        raise ValueError(message)


class EventGraph:
    def __init__(self, graph, program):
        require(graph['schema_version'] == 'moe-generic-event-dag/1', 'unsupported graph')
        self.graph, self.program = deepcopy(graph), deepcopy(program)
        self.events = {e['id']: e for e in graph['events']}
        self.work = {op['id']: op for op in program['operations']}
        require(len(self.events) == len(graph['events']), 'duplicate graph event')
        require(len(self.work) == len(program['operations']), 'duplicate work')
        require(len(self.events) <= 4096 and len(self.work) <= 1280, 'graph budget exceeded')
        for name, event in self.events.items():
            require(set(event) == {'id', 'deps', 'actions', 'callback'}, 'invalid graph event fields')
            require(len(set(event['deps'])) == len(event['deps']) and
                    set(event['deps']) <= self.events.keys(), 'unknown/duplicate predecessor')
            for action in event['actions']:
                require(set(action) == {'work', 'wait_before'} and action['work'] in self.work, 'unknown action')
                for pair in action['wait_before']:
                    self._validate_callback(pair)
            if event['callback'] is not None:
                self._validate_callback(event['callback'])
        reachable = set()
        while True:
            add = {n for n, e in self.events.items() if set(e['deps']) <= reachable} - reachable
            if not add:
                break
            reachable |= add
        require(reachable == self.events.keys(), 'cyclic event graph')
        require({a['work'] for e in self.events.values() for a in e['actions']} == self.work.keys(), 'work coverage mismatch')
        self.done, self.submitted, self.seen = set(), set(), {}
        self.log, self.submission_log = [], []
        self.now = self.decisions = self.steps = 0

    def _validate_callback(self, pair):
        require(isinstance(pair, list) and len(pair) == 2 and pair[0] in self.work, 'unknown callback work')
        allowed = ('local_admitted', 'local_started', 'completed') if self.work[pair[0]]['kind'] == 'local' else (
            'submitted', 'prepared', 'published', 'applied', 'request_acked', 'response', 'completed')
        require(pair[1] in allowed, 'invalid callback transition')

    def _batch(self, row):
        require(type(row['now_ns']) is int and row['now_ns'] >= self.now, 'nonmonotonic batch')
        self.now = row['now_ns']
        self.steps += 1
        require(self.steps <= 16384, 'logical step limit')
        for callback in row['events']:
            key = callback['operation'], callback['event']
            require(key[0] in self.submitted and key not in self.seen, 'unknown or duplicate callback')
            require(type(callback['time_ns']) is int and 0 <= callback['time_ns'] <= self.now, 'future callback')
            self._validate_callback(list(key))
            self.seen[key] = callback['time_ns']
        output = []
        changed = True
        while changed:
            changed = False
            ready = sorted(n for n, e in self.events.items() if n not in self.done and set(e['deps']) <= self.done)
            for name in ready:
                event = self.events[name]
                blocked = False
                for action in event['actions']:
                    if not all(tuple(p) in self.seen for p in action['wait_before']):
                        blocked = True
                        break
                    identity = action['work']
                    if identity not in self.submitted:
                        node = deepcopy(self.work[identity])
                        require(all((p['operation'], p['event']) in self.seen for p in node['after']),
                                'captured native proof precedes its callback')
                        self.submitted.add(identity)
                        self.submission_log.append(dict(operation=identity, time_ns=self.now))
                        output.append(node)
                        changed = True
                pair = event['callback']
                if blocked or (pair is not None and tuple(pair) not in self.seen):
                    continue
                self.done.add(name)
                record = dict(event=name, time_ns=self.now)
                if pair is not None:
                    record.update(native_operation=pair[0], native_event=pair[1], native_time_ns=self.seen[tuple(pair)])
                self.log.append(record)
                changed = True
        return output

    def advance(self, decision):
        self.decisions += 1
        require(self.decisions <= 256, 'physical decision limit')
        batches = decision['batches']
        require(1 <= len(batches) <= 64 and batches[-1]['now_ns'] == decision['now_ns'], 'invalid envelope')
        nodes = []
        for index, row in enumerate(batches):
            nodes = self._batch(row)
            require(index == len(batches)-1 or not nodes, 'historical batch hides a required submission')
        return nodes

    def wake_groups(self):
        """OR-of-AND callback frontier for the next possible work submission."""
        memo, visiting = {}, set()

        def missing(pairs):
            result = set()
            for p in pairs:
                key = tuple(p)
                if key in self.seen:
                    continue
                if key[0] not in self.submitted:
                    return None
                result.add(key)
            return result

        def needed(name):
            if name in self.done:
                return set()
            if name in memo:
                return memo[name]
            require(name not in visiting, 'cyclic frontier')
            visiting.add(name)
            event = self.events[name]
            result = set()
            for parent in event['deps']:
                value = needed(parent)
                if value is None:
                    result = None
                    break
                result |= value
            if result is not None:
                # An unsubmitted action cannot be enabled by a callback alone.
                if any(a['work'] not in self.submitted for a in event['actions']):
                    result = None
                elif event['callback'] is not None:
                    own = missing([event['callback']])
                    result = None if own is None else result | own
            visiting.remove(name)
            memo[name] = result
            return result

        groups = []
        for name, event in sorted(self.events.items()):
            if name in self.done:
                continue
            parents = set()
            for parent in event['deps']:
                value = needed(parent)
                if value is None:
                    parents = None
                    break
                parents |= value
            if parents is None:
                continue
            for action in event['actions']:
                waits = missing(action['wait_before'])
                if waits is None:
                    break
                if action['work'] not in self.submitted:
                    value = parents | waits
                    require(value, 'unscheduled action already enabled')
                    groups.append(frozenset(value))
                    break
        minimal = []
        for group in sorted(set(groups), key=lambda g: (len(g), sorted(g))):
            if not any(previous <= group for previous in minimal):
                minimal.append(group)
        return [[dict(operation=i, event=e) for i, e in sorted(g)] for g in minimal]

    def snapshot(self):
        return dict(all_events_fired=len(self.done) == len(self.events), events=self.log,
                    submitted_work=len(self.submitted), physical_decisions=self.decisions,
                    logical_steps=self.steps, submissions=self.submission_log)
