"""Control alarm admission must preserve state and lifetime/clock boundaries."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from moe_ub_cosim.transaction_session import _control_plan, NativeTransactionSession


class ControlAlarmTests(unittest.TestCase):
    def test_omitted_fields_and_zero_delay(self):
        self.assertEqual(_control_plan({},None,None,0,100),({},{}))
        state,message=_control_plan({},[dict(id=0,delay_ns=0)],None,17,100)
        self.assertEqual(state[0]['deadline_ns'],17)
        self.assertEqual(message,dict(alarms=[dict(id=0,delay_ns=0)]))

    def test_strict_ids_and_delays(self):
        for identity in (True,-1,65536,'1',[]):
            with self.subTest(identity=identity),self.assertRaises(ValueError):
                _control_plan({},[dict(id=identity,delay_ns=1)],None,0,100)
        for delay in (True,-1,90,91,1.5,'1'):
            with self.subTest(delay=delay),self.assertRaises(ValueError):
                _control_plan({},[dict(id=1,delay_ns=delay)],None,10,100)
        state,_=_control_plan({},[dict(id=65535,delay_ns=89)],None,10,100)
        self.assertEqual(state[65535]['deadline_ns'],99)

    def test_rejected_batch_does_not_mutate_prior_state(self):
        current={7:dict(armed_ns=0,deadline_ns=80,status='pending')};before=deepcopy(current)
        for alarms,cancel in [([dict(id=8,delay_ns=1),dict(id=True,delay_ns=1)],None),
                              ([dict(id=8,delay_ns=1)],[7,999]),
                              ([dict(id=8,delay_ns=1)],[8]),
                              ([dict(id=8,delay_ns=1),dict(id=8,delay_ns=2)],None)]:
            with self.assertRaises(ValueError):_control_plan(current,alarms,cancel,1,100)
            self.assertEqual(current,before)

    def test_cancellation_requires_pending_and_no_reuse(self):
        current={1:dict(armed_ns=0,deadline_ns=80,status='pending')}
        cancelled,_=_control_plan(current,None,[1],1,100)
        self.assertEqual(current[1]['status'],'pending')
        self.assertEqual(cancelled[1]['status'],'cancelled')
        for state in (cancelled,{1:dict(armed_ns=0,deadline_ns=80,status='fired')}):
            with self.assertRaises(ValueError):_control_plan(state,None,[1],81,100)
            with self.assertRaises(ValueError):_control_plan(state,[dict(id=1,delay_ns=1)],None,81,100)
        for ids in ([True],[999],[1,1],True):
            with self.assertRaises(ValueError):_control_plan(current,None,ids,1,100)

    def test_lifetime_budget_includes_cancelled_and_fired(self):
        state,_=_control_plan({},[dict(id=i,delay_ns=1) for i in range(32)],None,0,100)
        for i in state:state[i]['status']='fired' if i%2 else 'cancelled'
        with self.assertRaises(ValueError):_control_plan(state,[dict(id=32,delay_ns=1)],None,2,100)
        with self.assertRaises(ValueError):_control_plan({},[dict(id=i,delay_ns=1) for i in range(33)],None,0,100)

    def test_shape_rejections(self):
        for alarms in (True,{},[dict(id=1,delay_ns=1,operation=7)],[dict(id=1)]):
            with self.assertRaises(ValueError):_control_plan({},alarms,None,0,100)

    def test_receiver_rejects_forged_or_duplicate_alarm_without_state_change(self):
        bad_events=[
            [dict(alarm=8,event='fired',time_ns=20)],
            [dict(alarm=7,event='fired',time_ns=19)],
            [dict(alarm=7,event='completed',time_ns=20)],
            [dict(alarm=7,event='fired',time_ns=20),dict(alarm=7,event='fired',time_ns=20)],
            [dict(alarm=True,event='fired',time_ns=20)],
            [dict(alarm=7,event='fired',time_ns=20,operation=0)],
        ]
        for events in bad_events:
            session=NativeTransactionSession.__new__(NativeTransactionSession)
            session.pending=None;session.sequence=0;session.last_ns=0
            session.config=dict(operations=[]);session.p=dict(schema_version='moe-scaleout-rdma/1')
            session._alarms={7:dict(status='pending',deadline_ns=20)};session._delivered=set()
            session.logs=io.StringIO();session.transcript=io.StringIO()
            message=dict(type='DECIDE',decision_id=0,now_ns=20,events=events,drained=True)
            session._line=Mock(return_value='MOE_TX_EVENT '+json.dumps(message))
            with self.subTest(events=events),self.assertRaises(ValueError):session.receive()
            self.assertEqual(session._alarms[7]['status'],'pending')
            self.assertEqual(session._delivered,set())

    def test_commit_rejection_sends_no_valid_prefix(self):
        session=NativeTransactionSession.__new__(NativeTransactionSession)
        session.pending=dict(drained=True,now_ns=0);session.config=dict(operations=[])
        session._alarms={};session.p=dict(schema_version='moe-scaleout-rdma/1');session.t={}
        session.child=Mock();session.sequence=0;session.transcript=io.StringIO()
        with patch('moe_ub_cosim.transaction_session.validate'):
            with self.assertRaises(ValueError):
                session.commit([dict(id=0)],alarms=[dict(id=0,delay_ns=1),dict(id=True,delay_ns=1)])
        session.child.stdin.write.assert_not_called()
        self.assertEqual(session.config,dict(operations=[]))
        self.assertEqual(session._alarms,{})
        self.assertEqual(session.sequence,0)
        self.assertIsNotNone(session.pending)

    def test_finish_requires_explicit_pending_alarm_cancellation(self):
        session=NativeTransactionSession.__new__(NativeTransactionSession)
        session.pending=dict(drained=True,now_ns=20);session.config=dict(operations=[])
        session._alarms={7:dict(armed_ns=0,deadline_ns=100,status='pending')}
        session.p=dict(schema_version='moe-scaleout-rdma/1');session.t={}
        session.child=Mock();session.child.stdin.write.side_effect=len
        session.sequence=0;session.transcript=io.StringIO()
        with patch('moe_ub_cosim.transaction_session.validate'):
            with self.assertRaises(ValueError):session.commit([],finish=True)
            session.child.stdin.write.assert_not_called()
            self.assertEqual(session._alarms[7]['status'],'pending')
            session.commit([],finish=True,cancel_alarms=[7])
        message=json.loads(session.child.stdin.write.call_args.args[0].tobytes())
        self.assertTrue(message['finish'])
        self.assertEqual(message['cancel_alarms'],[7])
        self.assertEqual(session._alarms[7]['status'],'cancelled')
        self.assertIsNone(session.pending)


if __name__=='__main__':unittest.main()
