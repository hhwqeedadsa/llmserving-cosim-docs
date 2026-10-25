"""Observer plumbing with synthetic control only; actual networks are separate."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moe_ub_cosim.host_timing import HostTiming, read_trace
from moe_ub_cosim.modern_native import run, worker
from moe_ub_cosim.scaleout_rdma import save


class FakeController:
    def __init__(self,*_):
        self.layout=SimpleNamespace(plan={},description={},initial={})
        self.runtime=SimpleNamespace(snapshot=lambda:dict(all_events_fired=True))
        self.now=0;self.decisions=0;self.native_nodes=[];self.log=[]
    def advance(self,event):
        self.decisions+=1
        return []
    def snapshot(self):return dict(decisions=self.decisions,synthetic_only=True)


class FakeSession:
    def __init__(self,*_,timing):
        self.timing=timing;self.received=False;self.commands=[]
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def receive(self):
        if self.received:return None
        self.received=True
        return dict(now_ns=0,events=[],drained=True)
    def commit(self,nodes,**options):self.commands.append((deepcopy(nodes),options))


class SessionHostTimingTests(unittest.TestCase):
    def drive(self,directory,enabled,*,fail=False):
        for name in ('topology','protocol','config'):save(directory/(name+'.json'),{})
        observed=[]
        def create(*args,**kwargs):
            session=FakeSession(*args,**kwargs);observed.append(session);return session
        with patch('moe_ub_cosim.modern_native.ModernNativeController',FakeController), \
             patch('moe_ub_cosim.modern_native.NativeTransactionSession',side_effect=create):
            if fail:
                with patch.object(FakeController,'advance',side_effect=ValueError('preserved failure')):
                    with self.assertRaisesRegex(ValueError,'preserved failure'):
                        worker(*(directory/(n+'.json') for n in ('topology','protocol','config')),
                               directory/'unused-backend',directory,host_timing=enabled)
            else:
                worker(*(directory/(n+'.json') for n in ('topology','protocol','config')),
                       directory/'unused-backend',directory,host_timing=enabled)
        return observed[0]

    def test_disabled_worker_reads_no_observation_clocks_or_writes_traces(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root)
            with patch('moe_ub_cosim.host_timing.time.monotonic_ns',side_effect=AssertionError), \
                 patch('moe_ub_cosim.host_timing.time.process_time_ns',side_effect=AssertionError):
                session=self.drive(directory,False)
            self.assertEqual(session.commands,[([],dict(finish=True))])
            self.assertFalse((directory/'worker_host.jsonl').exists())

    def test_observer_does_not_add_control_commands(self):
        with tempfile.TemporaryDirectory() as root:
            a,b=Path(root)/'off',Path(root)/'on';a.mkdir();b.mkdir()
            off,on=self.drive(a,False),self.drive(b,True)
            self.assertEqual(off.commands,on.commands)
            self.assertEqual((a/'modern-execution.json').read_bytes(),(b/'modern-execution.json').read_bytes())
            trace=read_trace(b/'worker_host.jsonl')
            self.assertTrue(trace['trace_complete'])
            self.assertEqual(trace['phases']['controller_advance']['count'],1)
            self.assertEqual(trace['snapshots'][0]['values']['decisions'],1)

    def test_exception_keeps_modern_snapshot_and_incomplete_footer(self):
        with tempfile.TemporaryDirectory() as root:
            directory=Path(root);session=self.drive(directory,True,fail=True)
            self.assertEqual(session.commands,[])
            self.assertTrue((directory/'modern-execution.json').exists())
            trace=read_trace(directory/'worker_host.jsonl')
            self.assertTrue(trace['footer_present']);self.assertFalse(trace['trace_complete'])
            self.assertEqual(trace['open_spans'],[])

    def test_snapshot_disabled_does_not_read_clock(self):
        with patch('moe_ub_cosim.host_timing.time.monotonic_ns',side_effect=AssertionError):
            HostTiming().snapshot('disabled',{})

    def test_enabled_snapshot_roundtrips_with_clock_and_values(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'trace';observer=HostTiming(path)
            with observer.span('controller'):observer.snapshot('progress',dict(decisions=1))
            observer.close(True)
            trace=read_trace(path)
            self.assertTrue(trace['trace_complete'])
            self.assertEqual(trace['snapshots'][0]['values'],dict(decisions=1))

    def test_non_boolean_public_option_rejected_before_paths(self):
        for value in (1,None,'yes'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'host_timing must be boolean'):
                run(*['/does-not-exist']*5,host_timing=value)


if __name__=='__main__':unittest.main()
