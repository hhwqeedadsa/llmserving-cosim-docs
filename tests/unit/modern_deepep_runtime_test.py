"""Minimal causal counterexamples, without a provider or scripted timing."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime

ROOT = Path(__file__).resolve().parents[2]


def drain(runtime, withheld=(), reverse=False):
    # Functional test scheduler only. Every completion here is declared by the
    # test; this helper must never be used by network or research execution.
    while True:
        ready = [e for e in runtime.ready() if e not in withheld]
        if not ready:
            return
        runtime.fire(ready[-1] if reverse else ready[0])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/modern_deepep_partial_order/input.json').read_text())

    def pair_config(self, combine_warps=1):
        config = deepcopy(self.config)
        config['model']['experts'] = 4
        config['execution'].update(ranks=2, num_sms=1, dispatch_warps=2, combine_warps=combine_warps,
                                   call_contexts=3, requested_contexts=3, local_devices=[[0,1],[0,1]])
        config['calls'] = [dict(name='two', tokens=[[[2,3],[2,3]],[]])]
        return config

    def test_no_implicit_progress_or_early_submission(self):
        runtime = ModernDeepEPRuntime(self.config)
        initial = runtime.snapshot()
        self.assertEqual(initial['ready_events'],[f'm0.dispatch.r{r}.begin' for r in range(4)])
        for event in ('m0.slot.s0.d1.t0', 'm0.dispatch.data.s0.t0.d1.issued',
                      'm0.dispatch.data.s0.t0.d1.local_complete', 'm0.r0.experts_done'):
            with self.assertRaises(ValueError):runtime.fire(event)
        with self.assertRaises(ValueError):runtime.operation('m0.dispatch.data.s0.t0.d1')
        with self.assertRaises(ValueError):runtime.fire('absent')
        self.assertEqual(initial,runtime.snapshot())
        runtime.fire('m0.dispatch.r0.begin')
        with self.assertRaises(ValueError):runtime.fire('m0.dispatch.r0.begin')
        self.assertEqual(runtime.snapshot()['fired_events'],1)

    def test_slots_follow_actual_allocation_and_change_combine_connection(self):
        config = self.pair_config(combine_warps=2)
        observed = []
        for withheld in ('m0.slot.s0.d1.t1','m0.slot.s0.d1.t0'):
            runtime = ModernDeepEPRuntime(config)
            drain(runtime,[withheld]);runtime.fire(withheld);drain(runtime)
            state = runtime.snapshot()
            self.assertTrue(state['all_events_fired'])
            values = state['allocated_slots']
            self.assertEqual(set(values.values()),{0,1})
            self.assertEqual(len(values),2)
            operation = runtime.operation('m0.combine.data.s1.d0.t0')
            observed.append((values['m0.slot.s0.d1.t0'],operation['binding']['connection']))
            sent = runtime.operation('m0.dispatch.data.s0.t0.d1')
            self.assertEqual(sent['target']['byte_offset'],values['m0.slot.s0.d1.t0']*576)
        self.assertEqual(observed,[(0,0),(1,1)])

    def test_same_combine_channel_reduction_and_issue_follow_receive_index(self):
        runtime = ModernDeepEPRuntime(self.pair_config())
        held = 'm0.slot.s0.d1.t0'
        drain(runtime,[held]);runtime.fire(held)
        lower = 'm0.combine.data.s1.d0.t1'
        later_reduced = 'm0.combine.s1.d0.t0.reduced'
        held_issue = lower+'.issued'
        drain(runtime,[held_issue])
        self.assertIn(held_issue,runtime.ready())
        self.assertNotIn(later_reduced,runtime.ready())
        with self.assertRaises(ValueError):runtime.fire(later_reduced)
        runtime.fire(held_issue)
        self.assertIn(later_reduced,runtime.ready())
        drain(runtime)
        log = runtime.snapshot()['log']
        ordered = [row['receive_index'] for row in log
                   if row['kind']=='issued' and row['operation'].startswith('m0.combine.data.s1')]
        self.assertEqual(ordered,[0,1])

    def test_all_token_destination_slots_before_any_dispatch_put(self):
        runtime = ModernDeepEPRuntime(self.config)
        held = 'm0.slot.s0.d2.t1'
        drain(runtime,[held])
        self.assertIn('m0.slot.s0.d1.t1',runtime.snapshot()['allocated_slots'])
        self.assertNotIn('m0.dispatch.data.s0.t1.d1.issued',runtime.ready())
        with self.assertRaises(ValueError):runtime.fire('m0.dispatch.data.s0.t1.d1.issued')
        runtime.fire(held);drain(runtime)
        self.assertTrue(runtime.snapshot()['all_events_fired'])

    def test_delayed_token_cannot_allocate_next_iteration_on_same_role(self):
        config = self.pair_config()
        config['calls'][0]['tokens'][0].append([2,3])
        runtime = ModernDeepEPRuntime(config)
        held = 'm0.dispatch.s0.t0.prepared'
        drain(runtime,[held])
        state = runtime.snapshot()
        self.assertNotIn('m0.slot.s0.d1.t0',state['allocated_slots'])
        self.assertNotIn('m0.slot.s0.d1.t2',state['allocated_slots'])
        self.assertIn('m0.slot.s0.d1.t1',state['allocated_slots'])
        runtime.fire(held);drain(runtime)
        self.assertTrue(runtime.snapshot()['all_events_fired'])

    def test_local_completion_remote_visibility_and_final_signal_tail(self):
        for held in ('m0.dispatch.data.s0.t0.d1.local_complete',
                     'm0.dispatch.data.s0.t0.d1.visible',
                     'm0.dispatch.expert_count.s2.d1.visible'):
            runtime = ModernDeepEPRuntime(self.config)
            drain(runtime,[held])
            state = runtime.snapshot()
            self.assertFalse(state['all_application_complete'])
            fired = {row['event'] for row in state['log']}
            counterpart = ('m0.dispatch.data.s0.t0.d1.visible' if held.endswith('local_complete')
                           else 'm0.dispatch.data.s0.t0.d1.local_complete')
            self.assertIn(counterpart,fired)
            runtime.fire(held);drain(runtime)
            self.assertTrue(runtime.snapshot()['all_events_fired'])
        runtime = ModernDeepEPRuntime(self.config)
        tail = 'm1.combine.exit.s0.d1.local_complete'
        drain(runtime,[tail])
        self.assertTrue(runtime.snapshot()['all_application_complete'])
        self.assertFalse(runtime.snapshot()['all_events_fired'])
        self.assertIn(tail,runtime.ready())

    def test_full_reverse_schedule_empty_ranks_calls_and_input_isolation(self):
        runtime = ModernDeepEPRuntime(self.config)
        self.config['calls'].clear()
        drain(runtime,reverse=True)
        state = runtime.snapshot()
        self.assertTrue(state['all_application_complete'])
        self.assertTrue(state['all_events_fired'])
        self.assertEqual(state['fired_events'],1076)
        self.assertEqual(len(state['allocated_slots']),16)
        self.assertFalse(state['queues_executed'])
        state['log'].clear()
        self.assertEqual(len(runtime.snapshot()['log']),1076)
        empty = self.pair_config();empty['calls'][0]['tokens']=[[],[]]
        runtime = ModernDeepEPRuntime(empty);drain(runtime)
        self.assertTrue(runtime.snapshot()['all_events_fired'])
        self.assertEqual(runtime.snapshot()['allocated_slots'],{})


if __name__ == '__main__':
    unittest.main()
