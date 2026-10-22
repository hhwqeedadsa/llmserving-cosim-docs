"""Source-level counterexamples for dynamic slots, modern control and flush."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.modern_deepep_plan import compile_plan, resolve_combine_channel, validate, token_bytes, qp_mode
from moe_ub_cosim.deepep_plan import reachable

ROOT = Path(__file__).resolve().parents[2]


class ModernPlanTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/modern_deepep_partial_order/input.json').read_text())
        self.plan = compile_plan(self.config)
        self.ops = {op['id']:op for op in self.plan['operations']}
        self.events = {event['id']:event for event in self.plan['events']}

    def ancestors(self, identity):
        seen, todo = set(), [identity]
        while todo:
            for parent in self.events[todo.pop()]['deps']:
                if parent not in seen:
                    seen.add(parent);todo.append(parent)
        return seen

    def test_independent_data_and_count_enumeration(self):
        expected = set()
        for ci, call in enumerate(self.config['calls']):
            for src, tokens in enumerate(call['tokens']):
                for token, choices in enumerate(tokens):
                    for dst in range(4):
                        positions = tuple(i for i,e in enumerate(choices) if 2*dst <= e < 2*dst+2)
                        if positions:
                            expected.add((ci,'dispatch',src,dst,token,positions))
                            expected.add((ci,'combine',dst,src,token,positions))
        actual = {(o['call'],o['phase'],o['src'],o['dst'],o['token'],tuple(o['topk_positions']))
                  for o in self.ops.values() if o['kind']=='token_data'}
        self.assertEqual(expected,actual)
        self.assertEqual(len(expected),32)
        for ci,call in enumerate(self.config['calls']):
            for src,tokens in enumerate(call['tokens']):
                for dst in range(4):
                    count = sum(any(e in (2*dst,2*dst+1) for e in row) for row in tokens)
                    self.assertEqual(self.ops[f'm{ci}.dispatch.rank_count.s{src}.d{dst}']['value'],-count-1)
                    block = [-1-sum(e in row for row in tokens) for e in (2*dst,2*dst+1)]
                    self.assertEqual(self.ops[f'm{ci}.dispatch.expert_count.s{src}.d{dst}']['values'],block)
        # Two experts on one destination still produce one token put.
        self.assertEqual(self.ops['m0.dispatch.data.s0.t0.d1']['topk_positions'],[0,1])
        self.assertEqual(self.plan['layout']['dispatch_put_bytes'],576)
        self.assertEqual(token_bytes(7168,8,True),14464)
        self.assertEqual(token_bytes(7168,8,False),14400)

    def test_symbols_and_combine_nic_depend_on_actual_slots(self):
        op = self.ops['m0.combine.data.s1.d0.t0']
        self.assertNotIn('context_index',op['binding'])
        self.assertNotIn('slot_value',self.ops['m0.dispatch.data.s0.t0.d1']['target'])
        first = resolve_combine_channel(op['binding'],0,self.config['execution'],1)
        second = resolve_combine_channel(op['binding'],1,self.config['execution'],1)
        self.assertEqual((first['connection'],second['connection']),(0,1))
        self.assertEqual((first['local_device'],second['local_device']),(0,1))
        with self.assertRaises(ValueError):resolve_combine_channel(op['binding'],2,self.config['execution'],1)
        self.assertFalse(self.plan['scope']['dynamic_slots_resolved'])
        for op in self.ops.values():
            if op['kind']=='token_data' and op['phase']=='combine':
                for slot in range(op['binding']['source_to_owner_count']):
                    result = resolve_combine_channel(op['binding'],slot,self.config['execution'],op['src'])
                    self.assertLess(result['context_index'],5)

    def test_data_does_not_wait_for_remote_counts_or_visible_data(self):
        issued = 'm0.dispatch.data.s0.t0.d1.issued'
        parents = self.ancestors(issued)
        self.assertFalse(any('.rank_count.' in p or '.expert_count.' in p for p in parents))
        signal = self.ancestors('m0.dispatch.exit.s0.d1.issued')
        self.assertIn('m0.dispatch.data.s0.t0.d1.local_complete',signal)
        self.assertNotIn('m0.dispatch.data.s0.t0.d1.visible',signal)
        # Target-side provider visibility obligation is distinct from sender readiness.
        self.assertIn('m0.dispatch.data.s0.t0.d1.visible',
                      self.ancestors('m0.dispatch.exit.s0.d1.visible'))

    def test_flush_includes_prior_signal_tail(self):
        event = self.events['m0.combine.exit.r0.flush']
        self.assertIn('m0.dispatch.exit.s0.d1',event['covers_operations'])
        self.assertIn('m0.dispatch.entry.s0.d1',event['covers_operations'])
        self.assertNotIn('m0.combine.exit.s0.d1',event['covers_operations'])
        for rank in range(4):
            for phase in ('dispatch','combine'):
                for which in ('entry','exit'):
                    op = self.ops[f'm1.{phase}.{which}.s{rank}.d0']
                    self.assertEqual(op['binding']['context_index'],0)
                    self.assertEqual(op['target']['rolling_target'],
                                     5+(0 if phase=='dispatch' else 2)+(which=='exit'))

    def test_zero_counts_completion_and_phase_reuse(self):
        self.assertEqual(len(reachable(self.plan)),len(self.plan['events']))
        for held in ('m0.dispatch.expert_count.s2.d1.visible',
                     'm0.dispatch.data.s0.t0.d1.local_complete',
                     'm0.dispatch.data.s0.t0.d1.visible'):
            done = reachable(self.plan,[held])
            self.assertTrue(all(f'm1.combine.r{r}.complete' not in done for r in range(4)),held)
        done = reachable(self.plan,['m1.combine.exit.s0.d1.local_complete'])
        self.assertTrue(all(f'm1.combine.r{r}.complete' in done for r in range(4)))
        self.assertIn('m0.combine.r0.complete',self.ancestors('m1.dispatch.r0.begin'))
        self.assertEqual(self.ops['m1.dispatch.data.s0.t0.d1']['target']['epoch'],2)

    def test_illegal_modes_and_budgets(self):
        for key,value in [('mode','hybrid'),('expanded_layout',False),('allow_multiple_reduction',False),
                          ('expert_alignment',8),('connections',0),('num_sms',True),('call_contexts',6)]:
            config = deepcopy(self.config);config['execution'][key]=value
            with self.assertRaises(ValueError):validate(config)
        config = deepcopy(self.config);config['calls'][0]['tokens'][0][0]=[-1,2]
        with self.assertRaises(ValueError):validate(config)
        config = deepcopy(self.config);config['limits']['max_events']=1
        with self.assertRaises(ValueError):compile_plan(config)

    def test_rank_layout_and_shared_context_cases(self):
        config = deepcopy(self.config);config['model']['top_k']=4
        for call in config['calls']:
            call['tokens']=[[[0,1,2,3]],[],[],[]]
        plan = compile_plan(config)
        # R<=K uses owner-rank planes, rather than the first top-k position.
        returned = next(o for o in plan['operations'] if o['id']=='m0.combine.data.s1.d0.t0')
        self.assertEqual(returned['target']['byte_offset'],4*plan['layout']['combine_put_bytes'])
        self.assertEqual(qp_mode(0,0,2,1,2,True),(0,'gpu'))
        self.assertEqual(qp_mode(2,1,4,3,2,True),(2,'gpu'))
        self.assertEqual(qp_mode(1,3,2,5,4,False),(3,'cta'))


if __name__ == '__main__':
    unittest.main()
