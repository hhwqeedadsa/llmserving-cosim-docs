"""Independent small parameter cases and invalid input boundaries for M6."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan
from moe_ub_cosim.sequence import build_sequence

FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures/m6_execution_parameters'


class ExecutionParametersTest(unittest.TestCase):
    def test_nonuniform_top_three_and_idle_source(self):
        plan = build_plan(json.loads((FIXTURES / 'ep3.json').read_text()))
        self.assertEqual(plan['expert_input_counts'], [2, 3, 1])
        self.assertEqual(len(plan['transfers']), 15)
        self.assertEqual(sum(t['payload_bytes'] for t in plan['transfers']), 18432)
        expected = {('dispatch', 2, 7): [1, 2], ('dispatch', 7, 2): [0],
                    ('dispatch', 7, 12): [4], ('combine', 7, 2): [1, 2],
                    ('combine', 2, 7): [0], ('combine', 12, 7): [4]}
        for t in plan['transfers']:
            self.assertEqual(t['sources'][0]['expert_ids'], expected[t['kind'], t['src_rank'], t['dst_rank']])
        idle = plan['rank_graphs'][2]
        self.assertIsNone(idle['phase_nodes']['prepare'])
        self.assertEqual(next(n for n in idle['nodes'] if n['name'] == 'expert-compute')['duration_ns'], 4000)

    def test_top_one_and_no_local_expert_work(self):
        plan = build_plan(json.loads((FIXTURES / 'ep2.json').read_text()))
        self.assertEqual(plan['expert_input_counts'], [0, 2])
        self.assertEqual(len(plan['transfers']), 3)
        self.assertEqual(sum(t['payload_bytes'] for t in plan['transfers']), 1536)
        self.assertIsNone(plan['rank_graphs'][0]['phase_nodes']['expert'])
        self.assertEqual(plan['rank_to_ub_node'], [1, 3])

    def test_sizes_are_relational_not_case_list(self):
        for count in (1, 5, 6):
            c = json.loads((FIXTURES / 'ep2.json').read_text())
            c.update(rank_ids=list(range(count)), rank_to_ub_node=list(reversed(range(count))),
                     top_k=count, expert_placement=list(range(count)))
            c['tokens'] = [dict(c['tokens'][0], src_rank=0, expert_ids=list(range(count)))]
            p = build_plan(c)
            self.assertEqual(p['expert_input_counts'], [1] * count)
            self.assertEqual(sum(t['payload_bytes'] for t in p['transfers']), (count - 1) * 1536)

    def test_reject_invalid_configuration(self):
        base = json.loads((FIXTURES / 'ep3.json').read_text())
        changes = [lambda c: c.update(rank_ids=[]), lambda c: c.update(rank_ids=[2, 2, 12]),
                   lambda c: c.update(rank_ids=[2, 7, 2**31 - 1]),
                   lambda c: c.update(rank_to_ub_node=[0, 0, 2]),
                   lambda c: c.update(rank_to_ub_node=[0, 1]),
                   lambda c: c.update(rank_to_ub_node=[0, False, 2]),
                   lambda c: c.update(rank_to_ub_node=[0, 2**32, 2]),
                   lambda c: c.update(expert_placement=[]), lambda c: c.update(top_k=0),
                   lambda c: c.update(top_k=6), lambda c: c['tokens'][0].update(expert_ids=[1, 1, 2]),
                   lambda c: c['tokens'][0].update(expert_ids=[1, 2, 5]),
                   lambda c: c['tokens'][0].update(src_rank=0),
                   lambda c: c['tokens'].append(deepcopy(c['tokens'][0])),
                   lambda c: c['compute_profile_ns'].update(expert_per_input=2**62),
                   lambda c: c.update(hidden_dim=2**63 - 1)]
        for i, change in enumerate(changes):
            c = deepcopy(base); change(c)
            with self.subTest(case=i), self.assertRaises(ValueError): build_plan(c)
        p = build_plan(base)
        p['rank_to_ub_node'] = [0, 1, 2]
        with self.assertRaises(ValueError): validate_plan(p)
        p = build_plan(base)
        p['transfers'][0]['sources'][0]['expert_ids'] = [3]
        with self.assertRaises(ValueError): validate_plan(p)

    def test_sequence_keeps_members_mapping_and_token_count(self):
        c = json.loads((FIXTURES / 'ep3.json').read_text())
        second = deepcopy(c)
        second.update(graph_id='next', wave_id='D.w1')
        for t in second['tokens']: t['token_index'] += 1
        value = dict(schema_version='moe-ub-sequence-fixture/1', steps=[c, second])
        first, last = build_sequence(value)
        self.assertEqual(first['rank_to_ub_node'], last['rank_to_ub_node'])
        second['tokens'].pop()
        with self.assertRaises(ValueError): build_sequence(value)


if __name__ == '__main__':
    unittest.main()
