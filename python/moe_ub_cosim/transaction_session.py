"""Online MRT1 callbacks and incremental work on one native ns-3 clock.

NativeTransactionSession is a controller-facing API, not a GPU/GIN provider.
Use the bounded CLI for the dependency frontend, or run a custom Python driver
under the public host_probe with the same declared group/artifact limits.
"""
from copy import deepcopy
from contextlib import nullcontext
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time

from .scaleout_rdma import checked_json, digest, limits, monitored, save, ROOT
from .native_identity import locked_native_commit
from .scaleout_topology import fields, require
from .scaleout_transactions import validate
from .host_timing import HostTiming
from .static_stream_validation import StaticStreamValidationCache


def _control_plan(current, alarms, cancel_alarms, now_ns, max_ns):
    """Pure whole-command validation; no native events or GPU queue mutation."""
    candidate, message = deepcopy(current), {}
    if alarms is not None:
        require(isinstance(alarms, list) and len(current)+len(alarms) <= 32,
                'control alarm lifetime budget exceeded')
        for alarm in alarms:
            fields(alarm, 'id delay_ns', 'control alarm')
            identity, delay = alarm['id'], alarm['delay_ns']
            require(type(identity) is int and 0 <= identity <= 65535 and identity not in candidate,
                    'invalid or reused control alarm id')
            require(type(delay) is int and 0 <= delay < max_ns-now_ns,
                    'control alarm deadline reaches simulation cutoff')
            candidate[identity] = dict(armed_ns=now_ns, deadline_ns=now_ns+delay, status='pending')
        message['alarms'] = deepcopy(alarms)
    if cancel_alarms is not None:
        require(isinstance(cancel_alarms, list) and len(cancel_alarms) <= 32,
                'invalid control alarm cancellations')
        seen = set()
        for identity in cancel_alarms:
            require(type(identity) is int and identity in current and
                    current[identity]['status'] == 'pending' and identity not in seen,
                    'control alarm cancellation requires unique pending id')
            seen.add(identity)
            candidate[identity]['status'] = 'cancelled'
        message['cancel_alarms'] = deepcopy(cancel_alarms)
    return candidate, message


def _wake_plan(program, wake_on, delivered):
    """Pure validation of next-interaction subscriptions, including new work."""
    if wake_on is None:
        return {}
    from .scaleout_transactions import execution_limits
    counts = execution_limits(program)
    require(isinstance(wake_on, list) and len(wake_on) <= 7*(counts['max_remote_operations']+counts['max_local_operations']),
            'invalid session wakeup reference count')
    known = {node['id']: node for node in program['operations']}
    seen = set()
    for row in wake_on:
        fields(row, 'operation event', 'session wakeup')
        identity, event = row['operation'], row['event']
        require(type(identity) is int and 0 <= identity <= 65535 and identity in known and type(event) is str,
                'session wakeup references unsubmitted work or invalid type')
        allowed = ('local_admitted', 'local_started', 'completed') if known[identity]['kind'] == 'local' else (
            'submitted', 'published', 'applied', 'request_acked', 'response', 'completed')
        if 'submission_model' in program and known[identity]['kind'] == 'remote':
            allowed += ('prepared',)
        require(event in allowed, 'invalid session wakeup transition kind')
        if known[identity]['kind'] == 'remote' and event in ('request_acked', 'response'):
            connections = {connection['id']: connection for connection in program['connections']}
            require('self_service' not in connections[known[identity]['connection']],
                    'self service has no wire ACK or response callback')
            require(event != 'response' or known[identity]['verb'] == 'fetch_add', 'put has no response callback')
        key = identity, event
        require(key not in delivered and key not in seen, 'duplicate or already delivered session wakeup')
        seen.add(key)
    return dict(wake_on=deepcopy(wake_on))


def _group_plan(program, groups, delivered):
    from .scaleout_transactions import execution_limits
    counts = execution_limits(program)
    maximum = counts['max_remote_operations']+counts['max_local_operations']
    require(isinstance(groups, list) and len(groups) <= maximum, 'invalid session wakeup group count')
    require(all(isinstance(group, list) and group for group in groups), 'empty session wakeup conjunction')
    require(sum(map(len, groups)) <= 7*maximum, 'invalid session wakeup total reference count')
    unique = set()
    for group in groups:
        _wake_plan(program, group, delivered)
        key = frozenset((row['operation'], row['event']) for row in group)
        require(key not in unique, 'duplicate session wakeup group')
        unique.add(key)
    return dict(wake_groups=deepcopy(groups))


def _callback_batches(event, last_ns):
    """Validate the complete envelope before committing any receive state."""
    rows = event['batches']
    require(isinstance(rows, list) and 1 <= len(rows) <= 64, 'invalid callback batch count')
    flat, size, now, callbacks = [], 0, last_ns, 0
    for index, row in enumerate(rows):
        fields(row, 'now_ns events drained', 'session callback batch')
        require(type(row['now_ns']) is int and now <= row['now_ns'] <= event['now_ns'] and
                type(row['drained']) is bool and isinstance(row['events'], list), 'invalid callback batch time/type')
        encoded = len(json.dumps(row, separators=(',', ':'), ensure_ascii=False).encode())
        require(encoded <= 65536 and len(row['events']) <= 512, 'session callback single batch budget exceeded')
        require(index < 64 and size < 65536 and callbacks < 512, 'callback buffer was not flushed at high water')
        size += encoded
        callbacks += len(row['events'])
        require(size <= 131072 and callbacks <= 1024, 'session callback buffer budget exceeded')
        require(index == len(rows)-1 or not row['drained'], 'suppressed native drain boundary')
        previous = -1
        for transition in row['events']:
            require(isinstance(transition, dict) and type(transition.get('time_ns')) is int and
                    previous <= transition['time_ns'] <= row['now_ns'], 'invalid callback batch event order/time')
            require('alarm' not in transition or index == len(rows)-1, 'suppressed control alarm boundary')
            previous = transition['time_ns']
        flat.extend(row['events'])
        now = row['now_ns']
    require(flat == event['events'] and now == event['now_ns'] and rows[-1]['drained'] == event['drained'],
            'callback batch flattening or final boundary mismatch')
    return rows


class NativeTransactionSession:
    """Receive actual transitions, then commit newly constructed v4/v5 nodes.

    Host controller execution does not advance simulated time. No callback
    equates target applied, request ACK, returned value, or completion to GPU
    visibility. Cumulative work/connection budgets retain the v4 bounds.
    """

    def __init__(self, topology, protocol, initial, backend, output, *, timing=None, callback_batches=False,
                 reuse_static_validation=False):
        require(timing is None or isinstance(timing,HostTiming), 'invalid host timing observer')
        require(type(callback_batches) is bool, 'callback_batches must be boolean')
        require(type(reuse_static_validation) is bool, 'reuse_static_validation must be boolean')
        self._stream_cache = StaticStreamValidationCache() if reuse_static_validation else None
        self.callback_batches = callback_batches
        self.timing=timing if timing is not None else HostTiming()
        self.topology, self.protocol = Path(topology).resolve(), Path(protocol).resolve()
        self.backend, self.output = Path(backend).resolve(), Path(output).resolve()
        self.config = deepcopy(initial)
        require(self.config.get('schema_version') in ('moe-rdma-transactions/4', 'moe-rdma-transactions/5') and self.config.get('operations') == [],
                'session requires v4/v5 initial configuration with empty operations')
        self.t = checked_json(self.topology, 8388608)
        self.p = checked_json(self.protocol, 65536)
        validate(self.t, self.p, self.config, allow_no_remote=True, stream_cache=self._stream_cache)
        require(self.backend.is_file() and os.access(self.backend, os.X_OK), 'missing native transaction-session executable')
        require(self.output.is_dir() and not (self.output/'result.json').exists(), 'require an existing new session output directory')
        require(shutil.disk_usage(self.output).free >= 8*2**30, 'session output volume below 8GiB reserve')
        native_commit = locked_native_commit(ROOT)
        self.pending = None
        self.sequence = 0
        self.last_ns = 0
        self._delivered = set()
        self._alarms = {}
        self.child = None
        self._buffer = b''
        self.selector = selectors.DefaultSelector()
        self.started = time.monotonic()
        self.deadline = self.started + min(60, limits(self.p)['wall_seconds'])
        save(self.output/'initial.json', self.config)
        self.errors = (self.output/'native.stderr.log').open('w')
        self.logs = (self.output/'native.stdout.log').open('w', buffering=1)
        self.transcript = (self.output/'control.jsonl').open('w', buffering=1)
        argv = [str(self.backend), str(self.topology), str(self.protocol), str(self.output/'initial.json'), str(self.output/'result.json')]
        if self.timing.file is not None:argv.append(str(self.output/'native_host.jsonl'))
        if callback_batches:argv.append('--callback-batches')
        save(self.output/'native-command.json', dict(command=argv, cwd=str(self.output), native_commit=native_commit,
             native_commit_role='expected_source_checkout_not_binary_build_identity',
             input_hashes={str(p): digest(p) for p in (self.topology, self.protocol, self.backend, self.output/'initial.json')}))
        try:
            self.child = subprocess.Popen(argv, cwd=self.output, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=self.errors, bufsize=0)
            self.selector.register(self.child.stdout, selectors.EVENT_READ)
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _phase(self,name):
        timing=getattr(self,'timing',None)
        return timing.span(name) if timing is not None else nullcontext()

    def close(self):
        try:
            if self.child is not None:
                if self.child.stdin and not self.child.stdin.closed:
                    self.child.stdin.close()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.terminate()
                    try:
                        self.child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait(timeout=2)
                if self.child.stdout:
                    self.child.stdout.close()
        finally:
            self.selector.close()
            for handle in (self.errors, self.logs, self.transcript):
                handle.close()
            if self._stream_cache is not None:
                save(self.output/'static-validation.json', self._stream_cache.snapshot())

    def _line(self):
        while b'\n' not in self._buffer:
            remaining = self.deadline-time.monotonic()
            require(remaining > 0, 'transaction session host deadline exceeded')
            require(self.selector.select(remaining), 'transaction session host deadline exceeded')
            chunk = os.read(self.child.stdout.fileno(), 8192)
            if not chunk:
                require(not self._buffer, 'truncated native session output')
                code = self.child.wait(timeout=min(5, max(.01, remaining)))
                require(code == 0, 'native transaction session failed; inspect partial result and native.stderr.log')
                return None
            self._buffer += chunk
            require(len(self._buffer) <= 1048576, 'native session line budget exceeded')
        line, self._buffer = self._buffer.split(b'\n', 1)
        return line.decode('utf-8')

    def receive(self):
        """One DECIDE or terminal None. The pending DECIDE needs one commit."""
        require(self.pending is None, 'commit the pending decision before receiving again')
        while True:
            with self._phase('python_wait_line'):
                line = self._line()
            if line is None:
                return None
            self.logs.write(line+'\n')
            if not line.startswith('MOE_TX_EVENT '):
                continue
            event = json.loads(line[len('MOE_TX_EVENT '):])
            fields(event, 'type decision_id now_ns events drained'+(' batches' if getattr(self,'callback_batches',False) else ''), 'transaction session decision')
            require(event['type'] == 'DECIDE' and type(event['decision_id']) is int and
                    event['decision_id'] == self.sequence and type(event['now_ns']) is int and
                    self.last_ns <= event['now_ns'] <= limits(self.p)['max_sim_ns'] and
                    type(event['drained']) is bool and isinstance(event['events'], list), 'invalid native session decision')
            if getattr(self,'callback_batches',False):_callback_batches(event,self.last_ns)
            known = {op['id']: op for op in self.config['operations']}
            incoming = set()
            fired_alarms = []
            for row in event['events']:
                if isinstance(row, dict) and 'alarm' in row:
                    fields(row, 'alarm event time_ns', 'session control alarm transition')
                    identity = row['alarm']
                    require(type(identity) is int and identity in self._alarms and
                            self._alarms[identity]['status'] == 'pending' and row['event'] == 'fired' and
                            type(row['time_ns']) is int and row['time_ns'] == self._alarms[identity]['deadline_ns'] and
                            row['time_ns'] <= event['now_ns'], 'unknown or invalid control alarm transition')
                    key = ('alarm', identity, 'fired')
                    require(key not in self._delivered and key not in incoming, 'duplicate control alarm callback')
                    incoming.add(key)
                    fired_alarms.append(identity)
                    continue
                fields(row, 'operation event time_ns'+(' returned_value' if 'returned_value' in row else ''), 'session transition')
                require(type(row['operation']) is int and row['operation'] in known and type(row['time_ns']) is int and
                        0 <= row['time_ns'] <= event['now_ns'], 'unknown or future session transition')
                allowed = ('local_admitted', 'local_started', 'completed') if known[row['operation']]['kind'] == 'local' else (
                    'submitted', 'published', 'applied', 'request_acked', 'response', 'completed')
                if 'submission_model' in self.config and known[row['operation']]['kind'] == 'remote': allowed += ('prepared',)
                require(row['event'] in allowed, 'unknown session transition kind')
                key = row['operation'], row['event']
                require(key not in self._delivered and key not in incoming, 'duplicate session callback')
                incoming.add(key)
                if 'returned_value' in row:
                    require(row['event'] in ('response', 'completed') and type(row['returned_value']) is int and
                            0 <= row['returned_value'] < 2**64, 'invalid session return value')
            self._delivered.update(incoming)
            for identity in fired_alarms:
                self._alarms[identity]['status'] = 'fired'
            self.pending = event
            self.last_ns = event['now_ns']
            self.transcript.write(json.dumps(dict(direction='native', message=event))+'\n')
            return deepcopy(event)

    def commit(self, submissions, *, finish=False, alarms=None, cancel_alarms=None, wake_on=None, wake_groups=None):
        """Submit work or native-clock control wakeups, never device completion.

        Alarms do not consume a GPU slot. IDs are lifetime-unique in a separate
        namespace; finish needs explicit cancellation of any pending wakeups.
        wake_on lists actual transitions of existing or newly submitted work.
        Omission delivers every callback batch; [] waits for drain or alarms.
        Every commit replaces the policy. All callbacks retain their original
        order and timestamps and are delivered together at the next wakeup.
        """
        require(self.pending is not None and type(finish) is bool and isinstance(submissions, list), 'invalid session commit')
        require(wake_groups is None or getattr(self,'callback_batches',False), 'wake_groups requires callback batch mode')
        require(wake_on is None or not getattr(self,'callback_batches',False), 'wake_on incompatible with callback batch mode')
        with self._phase('python_candidate_copy'):
            candidate = deepcopy(self.config)
            candidate['operations'].extend(deepcopy(submissions))
        with self._phase('python_candidate_validate'):
            validate(self.t, self.p, candidate, allow_no_remote=True,
                     stream_cache=getattr(self, '_stream_cache', None))
            control, extra = _control_plan(self._alarms, alarms, cancel_alarms,
                                          self.pending['now_ns'], limits(self.p)['max_sim_ns'])
            if wake_on is not None:
                extra.update(_wake_plan(candidate, wake_on, self._delivered))
            if wake_groups is not None:
                extra.update(_group_plan(candidate, wake_groups, self._delivered))
        pending_alarms = any(a['status'] == 'pending' for a in control.values())
        require(not finish or (self.pending['drained'] and not submissions and
                (self.config['operations'] or self._alarms) and not pending_alarms and not alarms),
                'premature session finish or pending control alarm')
        require(finish or submissions or not self.pending['drained'] or pending_alarms,
                'empty idle session command cannot advance execution')
        message = dict(type='COMMIT', decision_id=self.sequence, now_ns=self.pending['now_ns'], submissions=submissions, finish=finish)
        message.update(extra)
        with self._phase('python_command_write'):
            raw = (json.dumps(message, separators=(',', ':'))+'\n').encode()
            require(len(raw) <= 1048576, 'session command exceeds 1MiB; split the batch')
            view = memoryview(raw)
            while view:
                written = self.child.stdin.write(view)
                require(written and written > 0, 'native session command pipe closed')
                view = view[written:]
            self.child.stdin.flush()
            self.transcript.write(json.dumps(dict(direction='frontend', message=message))+'\n')
        self.config = candidate
        self._alarms = control
        self.pending = None
        self.sequence += 1


def dependency_frontend(topology, protocol, config, backend, output, *, reuse_static_validation=False):
    """Known program stays in the frontend; submit only after actual events."""
    config = checked_json(Path(config), 4*2**20)
    initial = dict(config, operations=[])
    seen, submitted = set(), set()
    with NativeTransactionSession(topology, protocol, initial, backend, output,
                                  reuse_static_validation=reuse_static_validation) as session:
        while (event := session.receive()) is not None:
            for transition in event['events']:
                seen.add((transition['operation'], transition['event']))
            ready = []
            for op in config['operations']:
                if op['id'] not in submitted and all((d['operation'], d['event']) in seen for d in op['after']):
                    # Keep each whole command below the native line budget.
                    if len(json.dumps(ready+[op])) > 1000000:
                        break
                    ready.append(op)
            finished = not ready and len(submitted) == len(config['operations']) and event['drained']
            require(ready or finished or not event['drained'], 'frontend has no enabled node and native network is idle')
            session.commit(ready, finish=finished)
            submitted.update(op['id'] for op in ready)


def run(topology, protocol, config, backend, output, *, reuse_static_validation=False):
    require(type(reuse_static_validation) is bool, 'reuse_static_validation must be boolean')
    topology, protocol, config, backend, output = (Path(p).resolve() for p in (topology, protocol, config, backend, output))
    t, p, w = (checked_json(path, size) for path, size in
               ((topology/'topology.json', 8388608), (protocol, 65536), (config, 4*2**20)))
    require(w.get('schema_version') in ('moe-rdma-transactions/4', 'moe-rdma-transactions/5'), 'session frontend requires transactions/4 or /5')
    validate(t, p, w)
    require(backend.is_file() and os.access(backend, os.X_OK), 'missing transaction-session executable')
    native_commit = locked_native_commit(ROOT)
    output.mkdir(exist_ok=False)
    for name, value in (('topology.json', t), ('protocol.json', p), ('input.json', w)):
        save(output/name, value)
    save(output/'provenance.json', dict(native_commit=native_commit, frontend='actual_dependency_callbacks/1',
         native_commit_role='expected_source_checkout_not_binary_build_identity',
         hardware_calibrated=False, modern_deepep_connected=False,
         hashes={str(path): digest(path) for path in (topology/'topology.json', protocol, config, backend, Path(__file__))}))
    budget = limits(p)
    budget.update(wall_seconds=min(60, budget['wall_seconds']), rss_bytes=min(2**30, budget['rss_bytes']),
                  address_bytes=min(2**31, budget['address_bytes']), artifact_bytes=min(64*2**20, budget['artifact_bytes']))
    command = [sys.executable, '-m', 'moe_ub_cosim.transaction_session', '--worker',
               output/'topology.json', output/'protocol.json', output/'input.json', backend, output]
    if reuse_static_validation:
        command.append('--reuse-static-validation')
    monitored(command, output/'execution', [output], runtime=budget)
    result = checked_json(output/'result.json', budget['result_json_bytes'])
    require(result['passed'] and result['session']['finished'] and result['transactions']['transport']['physical_drained'], 'transaction session incomplete')
    return dict(passed=True, output=str(output), finish_ns=result['finish_ns'],
                completed_operations=result['completed_operations'], modern_deepep_connected=False)


if __name__ == '__main__':
    flags = sys.argv[7:]
    require(sys.argv[1:2] == ['--worker'] and len(sys.argv) >= 7 and len(set(flags)) == len(flags) and
            set(flags) <= {'--reuse-static-validation'},
            'internal session worker requires five paths and supported unique flags')
    dependency_frontend(*sys.argv[2:7], reuse_static_validation='--reuse-static-validation' in flags)
