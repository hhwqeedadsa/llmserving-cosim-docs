"""Validate online source confirmation deadlines, never infer receiver loss."""
import heapq
import json
from pathlib import Path

from .execution_events import configuration as event_configuration
from .scaleout_topology import fields, integer, require


def configuration(config):
    if 'source_ack_watchdog' not in config:
        return None
    value = config['source_ack_watchdog']
    fields(value, 'schema_version timeout_ns max_messages max_notifications', 'source ACK watchdog')
    require(value['schema_version'] == 'moe-source-ack-watchdog/1', 'unsupported source ACK watchdog schema')
    integer(value['timeout_ns'], 'watchdog timeout_ns', 1, 10**9)
    for key in ('max_messages', 'max_notifications'):
        integer(value[key], key, 1, 65536)
    require(config.get('schema_version') in ('moe-scaleout-service/1', 'moe-scaleout-service/2') and
            event_configuration(config) is not None and 'transport' in config,
            'source ACK watchdog requires service execution events and persistent transport')
    return value


class SourceAckObserver:
    """Consume notices beside the validated execution stream in actual order."""
    def __init__(self, config, execution):
        self.option = configuration(config)
        require(self.option is not None, 'missing source ACK watchdog configuration')
        self.execution = execution
        self.messages = {}
        self.pending = []
        self.count = self.completed = self.cancelled = 0
        self.now = -1

    def _clock(self, now):
        integer(now, 'watchdog event time', 0, 2**63-1)
        require(now >= self.now, 'watchdog event time moved backwards')
        # Cancelled/fired heap entries are discarded lazily, bounded by messages.
        while self.pending and not self.messages[self.pending[0][1]]['armed']:
            heapq.heappop(self.pending)
        require(not self.pending or self.pending[0][0] >= now,
                'source ACK deadline passed without online notification')

    def observe(self, event):
        """Called immediately after ExecutionObserver validates an OBSERVE."""
        require(self.execution.count == event['sequence']+1 and self.execution.now == event['time_ns'],
                'watchdog requires the current validated execution prefix')
        now, kind = event['time_ns'], event['kind']
        self._clock(now)
        if kind == 'message_send_post':
            mid = event['message']
            require(mid not in self.messages and len(self.messages) < self.option['max_messages'],
                    'source ACK watchdog message budget/identity violation')
            deadline = now+self.option['timeout_ns']
            require(deadline <= 2**63-1, 'source ACK watchdog deadline overflow')
            self.messages[mid] = dict(graph=event['graph_id'], rank=event['rank'], post=now,
                                      deadline=deadline, armed=True, notified=False, complete=False)
            heapq.heappush(self.pending, (deadline, mid))
        elif kind == 'message_send_complete':
            state = self.messages[event['message']]
            require(not state['complete'], 'duplicate watchdog confirmation')
            if state['armed']:
                self.cancelled += 1
            state['armed'] = False
            state['complete'] = True
            self.completed += 1
        elif kind == 'service_drained':
            require(all(s['complete'] for s in self.messages.values()), 'watchdog drained with unconfirmed sends')
        self.now = now

    def accept(self, notice):
        fields(notice, 'schema_version type sequence time_ns observed_events graph_id rank message send_post_ns deadline_ns',
               'source ACK timeout')
        require(notice['schema_version'] == 'moe-source-ack-timeout/1' and notice['type'] == 'SOURCE_ACK_TIMEOUT',
                'unsupported source ACK timeout frame')
        for key, hi in [('sequence', 65535), ('observed_events', 262144), ('rank', 511),
                        ('message', 2**64-1), ('send_post_ns', 2**63-1), ('deadline_ns', 2**63-1)]:
            integer(notice[key], key, 0, hi)
        now = notice['time_ns']
        self._clock(now)
        require(self.execution.drained_ns is None and now >= self.execution.now and
                notice['sequence'] == self.count and self.count < self.option['max_notifications'] and
                notice['observed_events'] == self.execution.count,
                'source ACK timeout prefix/order/budget violation')
        mid = notice['message']
        require(mid in self.messages, 'source ACK timeout before post')
        state = self.messages[mid]
        require(state['armed'] and not state['complete'] and not state['notified'] and
                notice['graph_id'] == state['graph'] and notice['rank'] == state['rank'] and
                notice['send_post_ns'] == state['post'] and notice['deadline_ns'] == state['deadline'] == now,
                'source ACK timeout identity/deadline/state violation')
        state['armed'] = False
        state['notified'] = True
        self.count += 1
        self.now = now

    def snapshot(self):
        return dict(schema_version='moe-source-ack-watchdog-report/1', configuration=dict(self.option),
                    messages=len(self.messages), source_completions=self.completed, cancelled_timers=self.cancelled,
                    notifications=self.count, pending_timers=sum(s['armed'] for s in self.messages.values()),
                    unconfirmed_messages=len(self.messages)-self.completed)


def audit(directory, result, frontend):
    from .execution_events import ExecutionObserver
    from .native_control import event as control_event
    from .scaleout_rdma import checked_json
    directory = Path(directory)
    config = checked_json(directory/'input.json', 2**20)
    option = configuration(config)
    native = result['service'].get('source_ack_watchdog')
    if option is None:
        require(native is None and not (directory/'source_ack_timeouts.jsonl').exists() and
                not (directory/'source_ack_watchdog.json').exists(), 'undeclared source ACK watchdog output')
        return None
    observer = ExecutionObserver(event_configuration(config))
    for row in frontend['history']:
        observer.register(checked_json(directory/'graphs'/row['graph_id']/'wave.json', 268435456))
    watchdog = SourceAckObserver(config, observer)
    recorded = directory/'source_ack_timeouts.jsonl'
    require(recorded.stat().st_size <= option['max_notifications']*1024, 'watchdog notice byte budget exceeded')
    count = 0
    # Preserve cross-stream same-nanosecond order and bind both saved streams
    # to their original native frames; a timeout never becomes a DECIDE.
    with (directory/'native.stdout.log').open() as raw, recorded.open() as notices, \
            (directory/'execution_events.jsonl').open() as events:
        for line in raw:
            require(len(line.encode()) <= 1048592, 'oversized native control log line')
            frame = control_event(line, observations=True, source_ack_timeouts=True, helpers='inflight_helpers' in config)
            if frame is None or frame['type'] == 'DECIDE':
                continue
            if frame['type'] == 'OBSERVE':
                require(json.loads(events.readline()) == frame, 'watchdog raw execution stream differs')
                observer.accept(frame)
                watchdog.observe(frame)
            elif frame['type']=='SOURCE_ACK_TIMEOUT':
                require(json.loads(notices.readline()) == frame, 'watchdog saved notice differs from raw frame')
                watchdog.accept(frame)
                count += 1
        require(not notices.read() and not events.read(), 'watchdog unbound saved records')
    summary = watchdog.snapshot()
    require(observer.drained_ns == result['finish_ns'] and summary == native and
            checked_json(directory/'source_ack_watchdog.json', 65536) == summary,
            'incomplete or mismatched source ACK watchdog')
    require(count == summary['notifications'], 'watchdog notice count differs')
    return summary
