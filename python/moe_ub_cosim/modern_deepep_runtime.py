"""Causal, untimed modern direct execution core with caller-supplied events.

No event fires automatically. In particular, ready is NOT a completion callback.
The caller chooses concurrent application steps; a future provider must supply
real issued/accepted/visible/local_complete callbacks on its own common clock.
The replay CLI only validates declared transcripts, never simulates a provider.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil

from .deepep_plan import encoded, fields, read, require
from .modern_deepep_plan import MAX_INPUT, MAX_OUTPUT, compile_plan, resolve_combine_channel, validate


class ModernDeepEPRuntime:
    """Public functional API. fire(id) commits exactly one enabled event.

    The configuration is copied and compiled here, so callers cannot mutate a
    live execution through their input. Slot allocation is a local atomic sender
    counter step, not packet arrival. No queue or buffer is released by this API.
    """

    def __init__(self, config):
        self._config = deepcopy(config)
        self._plan = compile_plan(self._config)
        self._events = {e['id']: e for e in self._plan['events']}
        self._ops = {o['id']: o for o in self._plan['operations']}
        self._slots = {s['id']: s for s in self._plan['slots']}
        self._deps = {e['id']: set(e['deps']) for e in self._plan['events']}
        self._successors = defaultdict(set)
        # Dispatch performs a warp sync after all destination slot atomics and
        # before any put in that token iteration. The symbolic DAG alone does
        # not enforce this collective boundary.
        token_slots = defaultdict(list)
        for slot in self._slots.values():
            token_slots[slot['call'], slot['src'], slot['token']].append(slot['id'])
        for op in self._ops.values():
            if op['phase'] == 'dispatch' and op['kind'] == 'token_data':
                self._deps[op['events']['issued']].update(token_slots[op['call'], op['src'], op['token']])
        for event, deps in self._deps.items():
            for parent in deps:
                self._successors[parent].add(event)
        self._remaining = {e: len(deps) for e, deps in self._deps.items()}
        self._ready = {e for e, count in self._remaining.items() if not count}
        self._done, self._slot_values = set(), {}
        self._group_next = defaultdict(int)
        self._owner_remaining = defaultdict(int)
        self._returns = defaultdict(list)
        for slot in self._slots.values():
            self._owner_remaining[slot['call'], slot['dst']] += 1
        for op in self._ops.values():
            if op['phase'] == 'combine' and op['kind'] == 'token_data':
                self._returns[op['call'], op['src']].append(op)
        self._resolved, self._log = {}, []

    def ready(self):
        """Enabled event IDs, sorted for inspection, NOT a scheduling policy."""
        return sorted(self._ready)

    def pending_dependencies(self):
        """Copy unfinished events and their unfinished direct prerequisites.

        Includes runtime-added slot/channel ordering. An empty list means an
        event is enabled, never that its external operation has completed.
        Taking or mutating this view cannot advance the runtime.
        """
        return {identity: sorted(self._deps[identity] - self._done)
                for identity in sorted(self._events.keys() - self._done)}

    def _add_dependency(self, event, parent):
        require(event not in self._done, 'dynamic order cannot modify a past event')
        if parent not in self._deps[event]:
            self._deps[event].add(parent)
            self._successors[parent].add(event)
            if parent not in self._done:
                self._remaining[event] += 1
                self._ready.discard(event)

    def _bind_owner(self, owner):
        channels = defaultdict(list)
        for op in self._returns[owner]:
            binding = op['binding']
            slot = self._slot_values[binding['slot_variable']]
            channel = resolve_combine_channel(binding, slot, self._config['execution'], op['src'])
            index = binding['source_rank_prefix'] + slot
            self._resolved[op['id']] = dict(binding=channel, receive_index=index)
            channels[channel['sm'], channel['rotated_warp']].append((index, op))
        for rows in channels.values():
            rows.sort(key=lambda row: row[0])
            for (_, previous), (_, current) in zip(rows, rows[1:]):
                # Source loop does reduction then put, before the next index.
                reduced, = self._events[current['events']['issued']]['deps']
                self._add_dependency(reduced, previous['events']['issued'])

    def operation(self, identity):
        """Copy an operation ONLY when issue is enabled or has actually fired.

        A future submission adapter can inspect the resolved binding/target
        before attempting a resource reservation; readiness does not reserve it.
        """
        require(identity in self._ops, 'unknown runtime operation')
        op = self._ops[identity]
        require(op['events']['issued'] in self._ready | self._done, 'operation is not ready to issue')
        result = deepcopy(op)
        if op['phase'] == 'dispatch' and op['kind'] == 'token_data':
            slot = self._slot_values[op['slot_variable']]
            result['target']['slot_value'] = slot
            result['target']['byte_offset'] = op['target']['byte_offset_base'] + slot*op['target']['byte_stride']
        elif op['phase'] == 'combine' and op['kind'] == 'token_data':
            require(identity in self._resolved, 'Combine owner has unresolved Dispatch slots')
            result.update(deepcopy(self._resolved[identity]))
        return result

    def fire(self, identity):
        """Record one actual caller event, rejecting early/duplicate callbacks."""
        require(isinstance(identity, str) and identity in self._events, 'unknown runtime event')
        require(identity not in self._done, 'runtime event fired twice')
        require(identity in self._ready, 'runtime event prerequisites have not fired')
        event = self._events[identity]
        row = dict(event=identity, kind=event['kind'])
        if event['kind'] == 'slot_allocate':
            slot = self._slots[identity]
            group = slot['call'], slot['src'], slot['dst']
            value = self._group_next[group]
            require(value < slot['group_size'], 'slot allocation group overflow')
            self._slot_values[identity] = value
            self._group_next[group] += 1
            row['slot_value'] = value
            owner = slot['call'], slot['dst']
            self._owner_remaining[owner] -= 1
            if not self._owner_remaining[owner]:
                self._bind_owner(owner)
        elif event['kind'] == 'issued':
            op = self.operation(event['operation'])
            row.update(operation=op['id'], binding=op['binding'], target=op['target'])
            if 'receive_index' in op:
                row['receive_index'] = op['receive_index']
        self._ready.remove(identity)
        self._done.add(identity)
        self._log.append(row)
        for successor in self._successors[identity]:
            self._remaining[successor] -= 1
            if not self._remaining[successor]:
                self._ready.add(successor)
        return deepcopy(row)

    def snapshot(self):
        completions = [e['id'] for e in self._plan['events'] if e['kind'] == 'application_stage_complete']
        return dict(schema_version='moe-modern-deepep-execution/1',
            scope='untimed caller-event execution; transcript is not a provider or hardware observation',
            plan_sha256=hashlib.sha256(encoded(self._plan)).hexdigest(),
            fired_events=len(self._done), total_events=len(self._events), ready_events=self.ready(),
            allocated_slots=dict(sorted(self._slot_values.items())),
            pending_application_completions=[e for e in completions if e not in self._done],
            all_application_complete=all(e in self._done for e in completions),
            all_events_fired=len(self._done) == len(self._events),
            log=deepcopy(self._log), simulated_packets=False, latency_predicted=False,
            hardware_calibrated=False, queues_executed=False, buffer_lifetime_validated=False)


def replay(config_path, actions_path, output):
    """Public CLI: validate an explicit full or prefix event transcript."""
    raw, config = read(Path(config_path).resolve(), MAX_INPUT)
    validate(config)
    actions_raw, actions = read(Path(actions_path).resolve(), MAX_OUTPUT)
    fields(actions, 'schema_version events', 'modern execution transcript')
    require(actions['schema_version'] == 'moe-modern-deepep-actions/1', 'unsupported action schema')
    require(isinstance(actions['events'], list) and len(actions['events']) <= config['limits']['max_events'],
            'runtime action count exceeds event budget')
    require(len(raw) <= config['limits']['max_input_bytes'], 'runtime input budget exceeded')
    output = Path(output).resolve()
    require(not output.exists() and output.parent.is_dir(), 'require new runtime output with existing parent')
    maximum = config['limits']['max_output_bytes']
    require(len(actions_raw) <= maximum, 'runtime transcript budget exceeded')
    require(shutil.disk_usage(output.parent).free >= 8*2**30+maximum, 'reserve 8GiB')
    runtime = ModernDeepEPRuntime(config)
    for identity in actions['events']:
        runtime.fire(identity)
    result = runtime.snapshot()
    result.update(passed=True, input_sha256=hashlib.sha256(raw).hexdigest(),
                  actions_sha256=hashlib.sha256(actions_raw).hexdigest())
    result_raw = encoded(result)
    require(len(result_raw)+len(raw)+len(actions_raw) <= maximum, 'runtime total output budget exceeded')
    output.write_bytes(result_raw)
    return dict(passed=True, output=str(output), fired_events=result['fired_events'],
                all_application_complete=result['all_application_complete'],
                all_events_fired=result['all_events_fired'], simulated_packets=False)
