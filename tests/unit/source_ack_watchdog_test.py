"""Artificial callback checks; actual ns-3 deadlines are checked separately."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from execution_events_test import OPTION, transcript, wave
from moe_ub_cosim.execution_events import ExecutionObserver
from moe_ub_cosim.native_control import event
from moe_ub_cosim.service_transport import native_arguments
from moe_ub_cosim.source_ack_watchdog import SourceAckObserver, configuration


def config(timeout=5):
    return dict(schema_version='moe-scaleout-service/1', transport={}, execution_events=OPTION,
                source_ack_watchdog=dict(schema_version='moe-source-ack-watchdog/1', timeout_ns=timeout,
                                         max_messages=4, max_notifications=4))


class WatchdogChecks(unittest.TestCase):
    def runtime(self, timeout=5):
        execution = ExecutionObserver(OPTION)
        execution.register(wave())
        return execution, SourceAckObserver(config(timeout), execution)

    def prefix(self, execution, watchdog, end):
        for row in transcript()[:end]:
            execution.accept(row)
            watchdog.observe(row)

    def notice(self, execution, now=7):
        return dict(schema_version='moe-source-ack-timeout/1', type='SOURCE_ACK_TIMEOUT', sequence=0,
                    time_ns=now, observed_events=execution.count, graph_id='g', rank=0,
                    message=7, send_post_ns=2, deadline_ns=now)

    def test_exact_deadline_during_silence_and_later_real_confirmation(self):
        execution, watchdog = self.runtime()
        self.prefix(execution, watchdog, 6)
        watchdog.accept(self.notice(execution))
        self.assertEqual(watchdog.snapshot()['unconfirmed_messages'], 1)
        self.assertIsNone(execution.messages[7]['send_complete'])
        self.assertIsNone(execution.messages[7]['recv_complete'])
        for row in transcript()[6:]:
            execution.accept(row);watchdog.observe(row)
        self.assertEqual(watchdog.snapshot(), dict(schema_version='moe-source-ack-watchdog-report/1',
            configuration=config()['source_ack_watchdog'], messages=1, source_completions=1,
            cancelled_timers=0, notifications=1, pending_timers=0, unconfirmed_messages=0))

    def test_normal_confirmation_cancels_without_notice(self):
        execution, watchdog = self.runtime(20)
        self.prefix(execution, watchdog, len(transcript()))
        self.assertEqual(watchdog.snapshot()['cancelled_timers'], 1)
        self.assertEqual(watchdog.snapshot()['notifications'], 0)
        with self.assertRaises(ValueError):watchdog.accept(self.notice(execution, 22))

    def test_received_input_does_not_cancel_source_confirmation_deadline(self):
        execution, watchdog = self.runtime(7)
        self.prefix(execution, watchdog, 9)
        watchdog.accept(self.notice(execution, 9))
        self.assertEqual(execution.messages[7]['recv_complete'], 8)
        self.assertIsNone(execution.messages[7]['send_complete'])
        self.assertEqual(watchdog.snapshot()['notifications'], 1)

    def test_same_time_actual_callback_order_both_legal(self):
        for timeout_first in (False, True):
            execution, watchdog = self.runtime(8)
            self.prefix(execution, watchdog, 9)
            if timeout_first:watchdog.accept(self.notice(execution, 10))
            execution.accept(transcript()[9]);watchdog.observe(transcript()[9])
            self.assertEqual(watchdog.snapshot()['notifications'], int(timeout_first))
            self.assertEqual(watchdog.snapshot()['cancelled_timers'], int(not timeout_first))
            with self.assertRaises(ValueError):watchdog.accept(self.notice(execution, 10))

    def test_missing_notice_not_reconstructed_after_the_fact(self):
        execution, watchdog = self.runtime()
        self.prefix(execution, watchdog, 6)
        execution.accept(transcript()[6])
        with self.assertRaisesRegex(ValueError, 'without online notification'):
            watchdog.observe(transcript()[6])

    def test_notice_identity_clock_prefix_and_fields_rejected(self):
        changes = [('rank', 1), ('message', 8), ('graph_id', 'other'), ('send_post_ns', 1),
                   ('deadline_ns', 8), ('time_ns', 6), ('time_ns', True), ('sequence', 1),
                   ('observed_events', 5), ('extra', 1), ('schema_version', 'other')]
        for field, value in changes:
            execution, watchdog = self.runtime()
            self.prefix(execution, watchdog, 6)
            before = deepcopy(watchdog.snapshot())
            with self.subTest(field=field), self.assertRaises(ValueError):
                watchdog.accept(dict(self.notice(execution), **{field: value}))
            self.assertEqual(watchdog.snapshot(), before)

    def test_duplicate_and_before_post_rejected(self):
        execution, watchdog = self.runtime()
        with self.assertRaises(ValueError):watchdog.accept(self.notice(execution))
        self.prefix(execution, watchdog, 6)
        watchdog.accept(self.notice(execution))
        with self.assertRaises(ValueError):watchdog.accept(dict(self.notice(execution), sequence=1))
        self.assertEqual(watchdog.count, 1)

    def test_notification_budget_keeps_pending_attempt(self):
        execution, watchdog = self.runtime()
        self.prefix(execution, watchdog, 6)
        watchdog.option = dict(watchdog.option, max_notifications=0)
        with self.assertRaises(ValueError):watchdog.accept(self.notice(execution))
        self.assertEqual(watchdog.snapshot()['pending_timers'], 1)
        self.assertIsNone(execution.messages[7]['send_complete'])

    def test_observe_requires_validated_current_prefix(self):
        execution, watchdog = self.runtime()
        with self.assertRaises(ValueError):watchdog.observe(transcript()[0])

    def test_configuration_strict_and_optional(self):
        self.assertIsNone(configuration({}))
        for field, value in [('timeout_ns', 0), ('timeout_ns', True), ('timeout_ns', 10**9+1),
                             ('max_messages', 65537), ('max_notifications', 0), ('extra', 1)]:
            c = config();c['source_ack_watchdog'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):configuration(c)
        for field in ('transport', 'execution_events', 'schema_version'):
            c = config();c.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):configuration(c)

    def test_control_frames_explicit_and_do_not_become_decisions(self):
        execution, watchdog = self.runtime()
        line = 'MOE_RDMA_EVENT '+json.dumps(self.notice(execution))+'\n'
        for options in ({}, dict(observations=True), dict(source_ack_timeouts=True)):
            with self.assertRaises(ValueError):event(line, **options)
        self.assertEqual(event(line, observations=True, source_ack_timeouts=True)['type'], 'SOURCE_ACK_TIMEOUT')

    def test_native_arguments_preserve_default_and_write_explicit_option(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(native_arguments({}, Path(folder)), [])
            args = native_arguments(config(), Path(folder))
            path = Path(folder)/'source-ack-watchdog-config.json'
            self.assertIn('--source-ack-watchdog', args)
            self.assertEqual(json.loads(path.read_bytes()), config()['source_ack_watchdog'])
            c = config();c.pop('transport')
            with self.assertRaises(ValueError):native_arguments(c, Path(folder))

    def test_cumulative_message_budget_checked_on_actual_post(self):
        execution, watchdog = self.runtime()
        self.prefix(execution, watchdog, 5)
        watchdog.option = dict(watchdog.option, max_messages=0)
        execution.accept(transcript()[5])
        with self.assertRaisesRegex(ValueError, 'message budget'):
            watchdog.observe(transcript()[5])
        self.assertFalse(watchdog.messages)


if __name__ == '__main__':unittest.main()
