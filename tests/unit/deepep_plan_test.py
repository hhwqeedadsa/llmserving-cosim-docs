"""Counterexamples for collective gates, peer/expert contexts and false completion."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.deepep_plan import compile_plan, reachable, execute, check

ROOT = Path(__file__).resolve().parents[2]


class DeepEPPlanChecks(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/deepep_partial_order/input.json').read_text())

    def test_independent_operation_oracle(self):
        plan = compile_plan(self.config)
        expected = []
        # Enumerate the mathematical source/token/expert relation independently
        # of generator event construction and its summary counters.
        for call, spec in enumerate(self.config['calls']):
            for rank, rows in enumerate(spec['tokens']):
                for token, choices in enumerate(rows):
                    for expert in choices:
                        owner = expert//2
                        expected.append((call, 'dispatch', 'data', rank, owner, expert, token, 14352))
                        expected.append((call, 'combine', 'data', owner, rank, expert, token, 14336))
                for expert in range(8):
                    owner = expert//2
                    expected.append((call, 'dispatch', 'count', rank, owner, expert, None, 4))
                    expected.append((call, 'combine', 'flag', owner, rank, expert, None, 4))
        actual = [(o['call'], o['phase'], o['kind'], o['src'], o['dst'], o['expert'], o['token'], o['operand_bytes'])
                  for o in plan['operations']]
        self.assertCountEqual(expected, actual)
        self.assertEqual(len(reachable(plan)), len(plan['events']))
        # The two selected experts on one destination retain two data puts.
        puts = [o for o in plan['operations'] if o['call'] == 0 and o['phase'] == 'dispatch'
                and o['kind'] == 'data' and o['src'] == 0 and o['dst'] == 1]
        self.assertEqual(len(puts), 2)
        self.assertNotEqual(puts[0]['qp_key'], puts[1]['qp_key'])

    def test_empty_expert_notification_blocks_later_collective(self):
        plan = compile_plan(self.config)
        notice = next(o for o in plan['operations'] if o['id'] == 'm0.d.count.s0.e4')
        self.assertEqual(notice['value'], -1)
        done = reachable(plan, [notice['events']['visible']])
        self.assertNotIn('m0.d.r2.complete', done)
        for rank in range(4):
            self.assertNotIn(f'm0.c.r{rank}.complete', done)
            self.assertNotIn(f'm1.d.r{rank}.begin', done)

    def test_one_flag_delays_its_consumer_without_global_return_barrier(self):
        plan = compile_plan(self.config)
        done = reachable(plan, ['m0.c.flag.d0.e2.visible'])
        self.assertNotIn('m0.c.r0.complete', done)
        self.assertNotIn('m1.d.r0.begin', done)
        for rank in (1, 2, 3):
            self.assertIn(f'm0.c.r{rank}.complete', done)
            self.assertIn(f'm1.d.r{rank}.begin', done)
        # They can issue next-call work but need rank 0's next counts to return.
        for rank in range(4):
            self.assertNotIn(f'm1.d.r{rank}.complete', done)

    def test_sender_feedback_is_not_gpu_visibility_or_buffer_release(self):
        plan = compile_plan(self.config)
        done = reachable(plan, ['m0.d.data.s0.t0.e2.sender_done'])
        self.assertTrue(all(e['id'] in done for e in plan['events'] if e['kind'] == 'kernel_complete'))
        self.assertFalse(plan['scope']['source_buffer_reuse_validated'])
        # Conversely, an unobserved GPU-visible count cannot be replaced by ACK.
        done = reachable(plan, ['m0.d.count.s0.e4.visible'])
        self.assertIn('m0.d.count.s0.e4.sender_done', done)
        self.assertNotIn('m0.d.r2.complete', done)

    def test_data_before_notification_and_context_across_phases_calls(self):
        plan = compile_plan(self.config)
        ops = {o['id']:o for o in plan['operations']}
        first = ops['m0.d.data.s0.t0.e2']
        second = ops['m1.d.data.s0.t0.e2']
        flag = ops['m0.c.flag.d1.e0']
        self.assertEqual(first['qp_key'], second['qp_key'])
        self.assertEqual(first['qp_key'], flag['qp_key'])
        self.assertEqual((first['buffer_epoch'], second['buffer_epoch']), (0, 2))
        self.assertEqual(first['buffer_index'], second['buffer_index'])
        done = reachable(plan, [first['events']['accepted']])
        self.assertNotIn('m0.d.count.s0.e2.visible', done)
        self.assertNotIn(flag['events']['accepted'], done)
        self.assertNotIn(second['events']['accepted'], done)

    def test_empty_rank_and_local_operations_keep_notifications(self):
        self.config['calls'] = [dict(name='idle', tokens=[[], [[2, 3]], [], []])]
        plan = compile_plan(self.config)
        self.assertEqual(sum(o['kind'] == 'count' for o in plan['operations']), 32)
        self.assertEqual(sum(o['kind'] == 'flag' for o in plan['operations']), 32)
        self.assertEqual(sum(o['kind'] == 'data' and o['path'] == 'rdma' for o in plan['operations']), 0)
        self.assertTrue(all(o['qp_key'] is None for o in plan['operations'] if o['path'] == 'local'))

    def test_target_buffer_consumer_precedes_reuse_via_collective_dependencies(self):
        plan = compile_plan(self.config)
        for rank in range(4):
            for phase, next_clean in [('d', f'm0.c.r{rank}.clean_next'),
                                      ('c', f'm1.d.r{rank}.clean_next')]:
                done = reachable(plan, [f'm0.{phase}.r{rank}.consume_done'])
                self.assertNotIn(next_clean, done)
                for op in plan['operations']:
                    expected = 'dispatch' if phase == 'd' else 'combine'
                    if op['call'] == 1 and op['phase'] == expected and op['dst'] == rank:
                        self.assertNotIn(op['events']['accepted'], done)

    def test_fp8_and_rejection_boundaries(self):
        self.config['model']['dispatch_format'] = 'fp8_float32_scales'
        plan = compile_plan(self.config)
        self.assertEqual(next(o['operand_bytes'] for o in plan['operations'] if o['kind'] == 'data'), 7408)
        for mutate in (lambda c:c['execution'].update(api='async'),
                       lambda c:c['execution'].update(nics_per_rank=2),
                       lambda c:c['limits'].update(max_events=3),
                       lambda c:c['calls'][0]['tokens'][0].__setitem__(0, [2, 2]),
                       lambda c:c['execution'].update(max_tokens_per_rank=True),
                       lambda c:c['model'].update(hidden_size=128),
                       lambda c:c['model'].update(experts=True),
                       lambda c:c['calls'][0].update(predicted_ready_ns=10)):
            config = deepcopy(self.config)
            mutate(config)
            with self.assertRaises(ValueError):
                compile_plan(config)

    def test_fresh_saved_artifact_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root/'input.json'
            config.write_text(json.dumps(self.config))
            execute(config, root/'plan')
            result = check(root/'plan', root/'checked.json', ['m0.c.flag.d0.e2.visible'])
            self.assertTrue(result['passed'])
            self.assertFalse(result['all_kernel_completions_reachable'])
            with self.assertRaises(ValueError):
                execute(config, root/'plan')
            tiny = deepcopy(self.config)
            tiny['limits']['max_output_bytes'] = 100
            (root/'tiny.json').write_text(json.dumps(tiny))
            with self.assertRaises(ValueError):
                execute(root/'tiny.json', root/'tiny-plan')
            self.assertFalse((root/'tiny-plan').exists())
            with self.assertRaises(ValueError):
                check(root/'plan', root/'unknown.json', ['not-an-event'])
            p = root/'plan/plan.json'
            p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaises(ValueError):
                check(root/'plan', root/'tampered.json')


if __name__ == '__main__':
    unittest.main()
