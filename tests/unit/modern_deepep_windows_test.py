"""Source lifetime, physical alias and integer-state counterexamples, no NIC."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.modern_deepep_windows import ModernDeepEPWindowRuntime, ModernDeepEPWindows, WindowReadLeases
from moe_ub_cosim.modern_deepep_plan import compile_plan

ROOT = Path(__file__).resolve().parents[2]


def drain(runtime, held=(), reverse=False):
    # TEST-ONLY event declarations. Not a GPU, queue or network scheduler.
    while True:
        ready = [e for e in runtime.ready() if e not in held]
        if not ready:return
        runtime.fire(ready[-1] if reverse else ready[0])


class WindowsTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/modern_deepep_partial_order/input.json').read_text())

    def test_main_allocation_aliases_epochs_and_signal_domain_is_separate(self):
        windows = ModernDeepEPWindows(self.config)
        description = windows.describe()
        self.assertEqual(description['workspace']['struct_bytes'],15831168)
        self.assertEqual(description['main_window']['ep_buffer_base'],16777216)
        self.assertEqual(description['main_window']['bytes'],18874368)
        self.assertEqual(description['counts']['send_base'],24640)
        self.assertEqual(description['counts']['recv_base'],49216)
        self.assertEqual(description['signals']['per_context_slots'],8)
        ops = {o['id']:o for o in compile_plan(self.config)['operations']}
        first = windows.resolve(ops['m0.dispatch.expert_count.s2.d1'])
        later = windows.resolve(ops['m1.dispatch.expert_count.s2.d1'])
        self.assertEqual(first['source']['byte_offset'],24640+(4+2)*8)
        self.assertEqual(first['target']['byte_offset'],49216+(4+4)*8)
        self.assertEqual(first['target']['byte_offset'],later['target']['byte_offset'])
        self.assertNotEqual(first['target']['epoch'],later['target']['epoch'])
        signal = windows.resolve(ops['m0.dispatch.entry.s0.d0'])
        self.assertIsNone(signal['source'])
        self.assertEqual(signal['target']['domain'],'provider_indexed_signals')
        self.assertNotIn('byte_offset',signal['target'])

    def test_overlapping_readers_block_write_even_across_epochs(self):
        leases = WindowReadLeases()
        source = dict(domain='main_window',owner=1,byte_offset=128,bytes=64,epoch=0)
        leases.acquire('a',source);leases.acquire('b',source)
        source['byte_offset']=4096
        overwrite = dict(domain='main_window',owner=1,byte_offset=160,bytes=32,epoch=7)
        for release in (None,'a'):
            if release:leases.release(release)
            before=leases.snapshot()
            with self.assertRaises(ValueError):leases.check_write(overwrite)
            self.assertEqual(leases.snapshot(),before)
        leases.check_write(dict(overwrite,owner=0))
        leases.check_write(dict(overwrite,byte_offset=192))
        leases.release('b');leases.check_write(overwrite)
        with self.assertRaises(ValueError):leases.release('b')
        with self.assertRaises(ValueError):leases.acquire('x',dict(source,domain='provider_indexed_signals'))
        for bad in (dict(source,bytes=-1),dict(source,owner=True),dict(source,byte_offset=-1)):
            with self.assertRaises(ValueError):leases.acquire('bad',bad)
            self.assertEqual(leases.snapshot(),{})

    def test_no_progress_and_rejected_events_leave_state_unchanged(self):
        runtime = ModernDeepEPWindowRuntime(self.config)
        before = runtime.snapshot()
        for event in ('absent','m0.dispatch.data.s0.t0.d1.visible'):
            with self.assertRaises(ValueError):runtime.fire(event)
            self.assertEqual(before,runtime.snapshot())
        runtime.fire('m0.dispatch.r0.begin')
        before = runtime.snapshot()
        with self.assertRaises(ValueError):runtime.fire('m0.dispatch.r0.begin')
        self.assertEqual(before,runtime.snapshot())

    def test_visible_data_does_not_release_its_source(self):
        runtime = ModernDeepEPWindowRuntime(self.config)
        op = 'm0.dispatch.data.s0.t0.d1'
        held = op+'.local_complete'
        drain(runtime,[held])
        state = runtime.snapshot()
        self.assertIn(op,state['memory_state']['active_source_leases'])
        self.assertIn(op+'.visible',{r['event'] for r in state['log']})
        self.assertFalse(state['all_application_complete'])
        runtime.fire(held);drain(runtime)
        self.assertEqual(runtime.snapshot()['memory_state']['active_source_leases'],{})
        self.assertTrue(runtime.snapshot()['all_events_fired'])
        concurrent=ModernDeepEPWindowRuntime(self.config)
        readers=['m0.dispatch.data.s0.t1.d1','m0.dispatch.data.s0.t1.d2']
        held=[op+'.local_complete' for op in readers];drain(concurrent,held)
        active=concurrent.snapshot()['memory_state']['active_source_leases']
        self.assertEqual(set(active),set(readers))
        self.assertEqual(active[readers[0]],active[readers[1]])
        concurrent.fire(held[0])
        self.assertEqual(set(concurrent.snapshot()['memory_state']['active_source_leases']),{readers[1]})
        concurrent.fire(held[1]);drain(concurrent)
        self.assertEqual(concurrent.snapshot()['memory_state']['active_source_leases'],{})

    def test_local_completion_can_precede_visibility(self):
        runtime = ModernDeepEPWindowRuntime(self.config)
        op = 'm0.dispatch.data.s0.t0.d1'
        held = op+'.visible'
        drain(runtime,[held])
        state = runtime.snapshot()
        self.assertNotIn(op,state['memory_state']['active_source_leases'])
        self.assertIn(op+'.local_complete',{r['event'] for r in state['log']})
        self.assertFalse(state['all_application_complete'])
        runtime.fire(held);drain(runtime)
        self.assertTrue(runtime.snapshot()['all_events_fired'])

    def test_consumed_self_count_does_not_release_still_read_source(self):
        runtime=ModernDeepEPWindowRuntime(self.config)
        op='m0.dispatch.expert_count.s2.d2'
        held=op+'.local_complete';drain(runtime,[held])
        memory=runtime.snapshot()['memory_state']
        self.assertIn(op,memory['active_source_leases'])
        self.assertTrue(any(row['rank']==2 and row['call']==0 for row in memory['consumed_counts']))
        self.assertEqual(memory['nonzero_count_elements'],0)
        runtime.fire(held);drain(runtime)
        self.assertEqual(runtime.snapshot()['memory_state']['active_source_leases'],{})

    def test_count_consume_resets_and_signal_generations_persist(self):
        runtime = ModernDeepEPWindowRuntime(self.config)
        drain(runtime,reverse=True)
        state = runtime.snapshot();memory=state['memory_state']
        self.assertTrue(state['all_events_fired'])
        self.assertEqual(memory['nonzero_count_elements'],0)
        self.assertEqual(len(memory['consumed_counts']),8)
        self.assertTrue(all(row['elements']==12 for row in memory['consumed_counts']))
        self.assertEqual(len(memory['completed_barriers']),32)
        self.assertEqual(len(memory['signals']),16)
        self.assertTrue(all(s['value']==s['shadow']==8 for s in memory['signals']))
        self.assertGreater(memory['peak_source_leases'],0)
        self.assertFalse(state['transport_payload_validated'])
        self.assertFalse(state['buffer_lifetime_validated'])
        signals=[r for r in state['memory_log'] if r['target']['domain']=='provider_indexed_signals']
        self.assertTrue(any(r['path']=='gin_self' for r in signals))

    def test_local_token_sources_and_cross_rank_staging(self):
        runtime = ModernDeepEPWindowRuntime(self.config);drain(runtime)
        rows=runtime.snapshot()['memory_log']
        local=[r for r in rows if r['path']=='local' and '.data.' in r['operation']]
        self.assertTrue(local)
        self.assertTrue(all(r['source']['domain'] in ('local_tma','local_reduction') for r in local))
        data=[r for r in rows if r['path']=='rdma' and '.data.' in r['operation']]
        self.assertTrue(all(r['source']['domain']=='main_window' for r in data))
        self.assertTrue(all(r['source']['owner']!=r['target']['owner'] for r in data))

    def test_empty_ranks_and_four_calls_do_not_reset_signals(self):
        config=deepcopy(self.config)
        call=dict(name='a',tokens=[[],[],[],[]])
        config['calls']=[dict(call,name=str(i)) for i in range(4)]
        runtime=ModernDeepEPWindowRuntime(config);drain(runtime)
        state=runtime.snapshot();memory=state['memory_state']
        self.assertTrue(state['all_events_fired'])
        self.assertEqual(memory['nonzero_count_elements'],0)
        self.assertEqual(len(memory['consumed_counts']),16)
        self.assertTrue(all(s['value']==s['shadow']==16 for s in memory['signals']))


if __name__=='__main__':unittest.main()
