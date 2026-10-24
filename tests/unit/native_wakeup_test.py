"""Subscriptions reference real registered work; rejection sends no prefix."""
from copy import deepcopy
import io
import unittest
from unittest.mock import Mock, patch

from moe_ub_cosim.transaction_session import _wake_plan, NativeTransactionSession


def program():
    return dict(schema_version='moe-rdma-transactions/5', submission_model={'mode': 'explicit_prefix/1'},
                connections=[dict(id=0)],
                operations=[dict(id=0, kind='remote', connection=0, verb='fetch_add'), dict(id=1, kind='local')])


class NativeWakeupTests(unittest.TestCase):
    def test_missing_and_empty_are_different_and_input_is_copied(self):
        self.assertEqual(_wake_plan(program(), None, set()), {})
        self.assertEqual(_wake_plan(program(), [], set()), dict(wake_on=[]))
        incoming = [dict(operation=0, event='prepared'), dict(operation=1, event='local_started')]
        message = _wake_plan(program(), incoming, set())
        incoming[0]['event'] = 'made_up'
        self.assertEqual(message['wake_on'][0]['event'], 'prepared')

    def test_reference_types_kinds_duplicates_and_delivered_rejected(self):
        cases = [True, {}, [dict(operation=True, event='completed')], [dict(operation=-1, event='completed')],
                 [dict(operation=65536, event='completed')], [dict(operation=2, event='completed')],
                 [dict(operation=0, event=True)], [dict(operation=0, event='local_started')],
                 [dict(operation=1, event='applied')], [dict(operation=0, event='completed', time_ns=1)],
                 [dict(operation=0)], [dict(operation=0, event='applied')]*2]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                _wake_plan(program(), rows, set())
        with self.assertRaisesRegex(ValueError, 'delivered'):
            _wake_plan(program(), [dict(operation=0, event='applied')], {(0, 'applied')})

    def test_prepared_requires_explicit_submission_and_budget_is_existing_work_bound(self):
        p = program()
        del p['submission_model']
        with self.assertRaises(ValueError):
            _wake_plan(p, [dict(operation=0, event='prepared')], set())
        with self.assertRaisesRegex(ValueError, 'reference count'):
            _wake_plan(program(), [dict(operation=0, event='completed')]*337, set())

    def test_whole_commit_rejected_without_alarm_or_work_mutation(self):
        session = NativeTransactionSession.__new__(NativeTransactionSession)
        session.pending = dict(drained=True, now_ns=0)
        session.config = dict(program(), operations=[])
        session._alarms, session._delivered = {}, set()
        session.p, session.t = dict(schema_version='moe-scaleout-rdma/1'), {}
        session.child, session.sequence, session.transcript = Mock(), 0, io.StringIO()
        before = deepcopy(session.config)
        with patch('moe_ub_cosim.transaction_session.validate'):
            with self.assertRaisesRegex(ValueError, 'unsubmitted'):
                session.commit([dict(id=0, kind='remote')], alarms=[dict(id=7, delay_ns=1)],
                               wake_on=[dict(operation=0, event='applied'), dict(operation=2, event='completed')])
        session.child.stdin.write.assert_not_called()
        self.assertEqual(session.config, before)
        self.assertEqual(session._alarms, {})
        self.assertEqual(session.sequence, 0)
        self.assertIsNotNone(session.pending)

    def test_impossible_put_and_self_wire_callbacks_are_rejected(self):
        p = program()
        p['operations'][0]['verb'] = 'put'
        with self.assertRaisesRegex(ValueError, 'put has no response'):
            _wake_plan(p, [dict(operation=0, event='response')], set())
        p['connections'][0]['self_service'] = dict(mode='declared_service_fifo/1')
        for event in ('response', 'request_acked'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'self service'):
                _wake_plan(p, [dict(operation=0, event=event)], set())


if __name__ == '__main__':
    unittest.main()
