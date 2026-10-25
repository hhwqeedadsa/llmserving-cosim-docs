"""Admission before mutation and preservation of historical advancement times."""
from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch
from moe_ub_cosim.transaction_session import _callback_batches, _group_plan, NativeTransactionSession
from moe_ub_cosim.modern_native import ModernNativeController


def program():
    return dict(schema_version='moe-rdma-transactions/5',submission_model={},connections=[dict(id=0)],
                operations=[dict(id=0,kind='remote',connection=0,verb='fetch_add'),dict(id=1,kind='local')])


def envelope():
    batches=[dict(now_ns=5,events=[dict(operation=0,event='submitted',time_ns=2)],drained=False),
             dict(now_ns=9,events=[dict(operation=0,event='prepared',time_ns=7)],drained=False)]
    return dict(type='DECIDE',decision_id=1,now_ns=9,events=sum([r['events'] for r in batches],[]),drained=False,batches=batches)


class CallbackBatchTests(unittest.TestCase):
    def test_group_admission_types_duplicates_impossible_and_total_bounds(self):
        groups=[[dict(operation=0,event='applied'),dict(operation=1,event='completed')]]
        result=_group_plan(program(),groups,set());groups[0].clear()
        self.assertEqual(len(result['wake_groups'][0]),2)
        self.assertEqual(_group_plan(program(),[],set()),dict(wake_groups=[]))
        for bad in (True,{},[[]],[[dict(operation=2,event='completed')]],
                    [[dict(operation=0,event='response')]]*2,[[dict(operation=1,event='applied')]],
                    [[dict(operation=0,event='applied')]*337]):
            with self.subTest(bad=bad),self.assertRaises(ValueError):_group_plan(program(),bad,set())
        with self.assertRaisesRegex(ValueError,'delivered'):
            _group_plan(program(),[[dict(operation=0,event='applied')]],{(0,'applied')})

    def test_envelope_preserves_times_and_rejects_flat_order_drain_alarm_and_bounds(self):
        original=envelope();self.assertEqual(_callback_batches(original,0),original['batches'])
        changes=[lambda e:e['batches'][0].update(now_ns=10),lambda e:e['batches'][-1].update(now_ns=8),
                 lambda e:e['batches'][0].update(drained=True),lambda e:e['events'].reverse(),
                 lambda e:e['batches'][0]['events'][0].update(time_ns=6),
                 lambda e:e['batches'][0]['events'][0].update(alarm=7),
                 lambda e:e.update(batches=e['batches']*33),
                 lambda e:e['batches'][0].update(events=[dict(time_ns=0)]*513),
                 lambda e:e['batches'][0]['events'][0].update(padding='x'*65536)]
        for change in changes:
            e=deepcopy(original);change(e)
            with self.subTest(change=change),self.assertRaises(ValueError):_callback_batches(e,0)
        same=deepcopy(original);same['batches'][0]['now_ns']=9
        self.assertEqual(_callback_batches(same,0),same['batches'])

    def test_invalid_receive_does_not_mutate_delivered_or_pending(self):
        s=NativeTransactionSession.__new__(NativeTransactionSession)
        s.pending=None;s.callback_batches=True;s.sequence=1;s.last_ns=0;s.config=program()
        s.p={};s._alarms={};s._delivered=set();s.logs=io.StringIO();s.transcript=io.StringIO()
        e=envelope();e['batches'][0]['now_ns']=10
        s._line=Mock(return_value='MOE_TX_EVENT '+json.dumps(e))
        with self.assertRaises(ValueError):s.receive()
        self.assertEqual(s._delivered,set());self.assertIsNone(s.pending);self.assertEqual(s.last_ns,0)

    def test_group_or_old_mode_rejection_writes_no_command(self):
        for enabled,extra in ((False,dict(wake_groups=[])),(True,dict(wake_on=[]))):
            s=NativeTransactionSession.__new__(NativeTransactionSession)
            s.pending=dict(drained=False,now_ns=0);s.callback_batches=enabled;s.child=Mock()
            with self.assertRaises(ValueError):s.commit([],**extra)
            s.child.stdin.write.assert_not_called()

    def test_controller_counts_physical_and_logical_and_keeps_batch_times(self):
        c=ModernNativeController.__new__(ModernNativeController)
        c.callback_batches=True;c.now=0;c.decisions=0;c.logical_steps=0;seen=[]
        def step(row):
            seen.append((row['now_ns'],row['events'][0]['time_ns']));c.now=row['now_ns'];return []
        c._advance_step=step;c.runtime=Mock()
        self.assertEqual(c.advance_batches(envelope()),[])
        self.assertEqual(seen,[(5,2),(9,7)])
        self.assertEqual((c.decisions,c.logical_steps),(1,2))
        c._advance_step=Mock(return_value=[dict(id=9)])
        e=envelope();c.now=0
        with self.assertRaisesRegex(ValueError,'earlier native submission'):c.advance_batches(e)
        self.assertEqual(c._advance_step.call_count,1)

    def test_physical_limit_not_raised(self):
        c=ModernNativeController.__new__(ModernNativeController)
        c.callback_batches=True;c.now=0;c.decisions=256;c.logical_steps=0;c._advance_step=Mock()
        with self.assertRaisesRegex(ValueError,'decision limit'):c.advance_batches(envelope())
        c._advance_step.assert_not_called()


if __name__=='__main__':unittest.main()
