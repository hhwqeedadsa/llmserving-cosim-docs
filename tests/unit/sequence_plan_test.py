"""Run identity allocation, exhaustion and fixed next-step input validation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import RunIdentities, build_plan
from moe_ub_cosim.sequence import build_sequence


class SequenceTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((Path(__file__).resolve().parents[2] /
                                 'fixtures/m2_sequence/two_steps.json').read_text())

    def test_reused_local_nodes_disjoint_run_ids(self):
        first, second = build_sequence(self.config)
        for key in ('transfer_id', 'ub_task_id', 'comm_tag'):
            self.assertFalse({t[key] for t in first['transfers']} & {t[key] for t in second['transfers']})
        self.assertEqual([[n['node_id'] for n in g['nodes']] for g in first['rank_graphs']],
                         [[n['node_id'] for n in g['nodes']] for g in second['rank_graphs']])
        self.assertEqual([sum(t['payload_bytes'] for t in p['transfers']) for p in (first, second)], [114688] * 2)
        self.assertEqual(build_sequence(self.config), [first, second])

    def test_exhaustion_is_atomic(self):
        for field, maximum in (('transfer', 2**64 - 1), ('task', 2**32 - 1), ('tag', 499999999)):
            allocator = RunIdentities(**{field: maximum - 13})
            build_plan(self.config['steps'][0], allocator)
            before = deepcopy(allocator.__dict__)
            with self.assertRaises(ValueError):
                build_plan(self.config['steps'][1], allocator)
            self.assertEqual(allocator.__dict__, before)
            allocator = RunIdentities(**{field: maximum - 12})
            before = deepcopy(allocator.__dict__)
            with self.assertRaises(ValueError):
                build_plan(self.config['steps'][0], allocator)
            self.assertEqual(allocator.__dict__, before)

    def test_repeated_identities(self):
        for field in ('graph_id', 'wave_id'):
            config = deepcopy(self.config)
            config['steps'][1][field] = config['steps'][0][field]
            with self.assertRaises(ValueError):
                build_sequence(config)

    def test_invalid_sequence(self):
        changes = [lambda c: c['steps'].pop(),
                   lambda c: c['steps'][1].update(ready_ns=1),
                   lambda c: c['steps'][1].update(send_slots_per_rank=2),
                   lambda c: c['steps'][1].update(graph_id='graph,ambiguous'),
                   lambda c: c['steps'][1]['tokens'][0].update(token_index=0),
                   lambda c: c['steps'][1]['tokens'][0].update(request_id='other')]
        for change in changes:
            config = deepcopy(self.config); change(config)
            with self.assertRaises(ValueError):
                build_sequence(config)


if __name__ == '__main__':
    unittest.main()
