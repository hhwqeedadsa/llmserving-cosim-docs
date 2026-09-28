"""Independent arithmetic and structural checks for the fixed M2.1 layer."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan

ROOT = Path(__file__).resolve().parents[2]


class LayerPlanTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'fixtures/m2_single_layer/layer.json').read_text())
        self.plan = build_plan(self.config)

    def test_fixed_matrix_and_local_work(self):
        expected = [[0, 8192, 8192, 0], [8192, 0, 8192, 0],
                    [0, 0, 0, 8192], [8192, 8192, 0, 0]]
        for kind in ('dispatch', 'combine'):
            matrix = [[0] * 4 for _ in range(4)]
            transfers = [t for t in self.plan['transfers'] if t['kind'] == kind]
            self.assertEqual(len(transfers), 7)
            self.assertEqual(sum(t['payload_bytes'] for t in transfers), 57344)
            for t in transfers:
                matrix[t['src_rank']][t['dst_rank']] += t['payload_bytes']
                self.assertEqual(t['chunk_index'], 0)
                self.assertGreater(t['transfer_id'], 2**32)
                self.assertEqual(len(t['sources']), 1)
            self.assertEqual(matrix, expected if kind == 'dispatch' else [list(row) for row in zip(*expected)])
        self.assertEqual(self.plan['expert_input_counts'], [2, 2, 3, 1])
        work = []
        for graph in self.plan['rank_graphs']:
            expert = next(n for n in graph['nodes'] if n['name'] == 'expert-compute')
            self.assertEqual(expert['duration_ns'], [8000, 8000, 12000, 4000][graph['rank_id']])
            work.extend((v['request_id'], v['expert_id'], v['local']) for v in expert['expert_inputs'])
        self.assertCountEqual(work, [('A', 2, False), ('A', 4, False), ('B', 0, False), ('B', 5, False),
                                     ('C', 4, True), ('C', 6, False), ('D', 1, False), ('D', 3, False)])
        self.assertEqual(sum(len(g['nodes']) for g in self.plan['rank_graphs']), 48)

    def test_local_dependencies_and_send_order(self):
        transfers = {t['transfer_id']: t for t in self.plan['transfers']}
        for graph in self.plan['rank_graphs']:
            nodes = {n['node_id']: n for n in graph['nodes']}
            phases = graph['phase_nodes']
            self.assertEqual(nodes[phases['prepare']]['deps'], [])
            for kind, entry, exit_node in [('dispatch', 'prepare', 'expert'), ('combine', 'local_merge', 'finish')]:
                comms = [n for n in nodes.values() if n['kind'] != 'COMP' and transfers[n['transfer_id']]['kind'] == kind]
                self.assertEqual(set(nodes[phases[exit_node]]['deps']), {phases[entry]} | {n['node_id'] for n in comms})
                for node in comms:
                    self.assertEqual(node['deps'], [phases[entry]])
                destinations = [transfers[n['transfer_id']]['dst_rank'] for n in comms if n['kind'] == 'SEND']
                self.assertEqual(destinations, sorted(destinations))
            self.assertEqual(nodes[phases['local_merge']]['deps'], [phases['expert']])
            self.assertEqual(nodes[phases['next_layer']]['deps'], [phases['finish']])

    def test_deterministic(self):
        self.assertEqual(self.plan, build_plan(deepcopy(self.config)))
        self.assertNotIn('finish_ns', json.dumps(self.plan))

    def test_invalid_inputs(self):
        variants = [('schema_version', 'bad'), ('hidden_dim', -1), ('hidden_dim', 2**32),
                    ('dispatch_element_bytes', 0), ('send_slots_per_rank', 3),
                    ('application_metadata_bytes', 1), ('chunk_bytes', 0), ('ready_ns', 2**63),
                    ('top_k', True), ('template', 'invented'), ('rank_ids', [False, 1, 2, 3]),
                    ('expert_placement', [0] * 8)]
        for field, value in variants:
            with self.subTest(field=field, value=value):
                bad = deepcopy(self.config); bad[field] = value
                with self.assertRaises(ValueError):
                    build_plan(bad)
        for field, value in [('expert_ids', [2]), ('expert_ids', [2, 2]), ('expert_ids', [2, 99]),
                             ('layer_id', 8), ('token_index', -1), ('src_rank', 4), ('phase', 'prefill')]:
            with self.subTest(field=field, value=value):
                bad = deepcopy(self.config); bad['tokens'][0][field] = value
                with self.assertRaises(ValueError):
                    build_plan(bad)

    def test_plan_pairing_identity_and_dependencies(self):
        changes = [lambda p: p['transfers'][1].update(comm_tag=p['transfers'][0]['comm_tag']),
                   lambda p: p['transfers'][1].update(ub_task_id=p['transfers'][0]['ub_task_id']),
                   lambda p: p['transfers'][1].update(transfer_id=p['transfers'][0]['transfer_id']),
                   lambda p: p['transfers'][0].update(payload_bytes=2**32),
                   lambda p: p['transfers'][0].update(dst_rank=p['transfers'][0]['src_rank']),
                   lambda p: p['transfers'][0].update(operation_id='absent'),
                   lambda p: p['rank_graphs'][0]['nodes'][1].update(deps=[999]),
                   lambda p: p['rank_graphs'][0]['nodes'][1].update(node_id=1),
                   lambda p: p['rank_graphs'][0]['nodes'][1].update(transfer_id=99),
                   lambda p: p['rank_graphs'][0]['nodes'][1].update(rank_id=1),
                   lambda p: p['transfers'][0].update(send_node_id=999)]
        for change in changes:
            bad = deepcopy(self.plan); change(bad)
            with self.assertRaises(ValueError):
                validate_plan(bad)

    def test_dedup_keeps_both_experts(self):
        self.config['tokens'][0]['expert_ids'] = [4, 5]
        plan = build_plan(self.config)
        self.assertEqual(plan['expert_input_counts'], [2, 1, 4, 1])
        self.assertEqual(len(plan['transfers']), 12)
        self.assertEqual(sum(t['payload_bytes'] for t in plan['transfers']), 98304)
        dispatch = [t for t in plan['transfers'] if t['kind'] == 'dispatch' and t['src_rank'] == 0]
        self.assertEqual(len(dispatch), 1)
        self.assertEqual((dispatch[0]['dst_rank'], dispatch[0]['payload_bytes']), (2, 8192))
        self.assertEqual(dispatch[0]['sources'][0]['expert_ids'], [4, 5])
        expert = next(n for n in plan['rank_graphs'][2]['nodes'] if n['name'] == 'expert-compute')
        self.assertEqual(expert['duration_ns'], 16000)
        self.assertCountEqual([(v['request_id'], v['expert_id']) for v in expert['expert_inputs']],
                              [('A', 4), ('A', 5), ('B', 5), ('C', 4)])

    def test_chunks_and_send_slots(self):
        self.config['chunk_bytes'] = 3072
        one = build_plan(self.config)
        self.assertEqual(len(one['transfers']), 42)
        self.assertEqual(sum(t['payload_bytes'] for t in one['transfers']), 114688)
        self.assertEqual(one['expert_input_counts'], [2, 2, 3, 1])
        for start in range(0, 42, 3):
            parts = one['transfers'][start:start + 3]
            self.assertEqual([p['payload_bytes'] for p in parts], [3072, 3072, 2048])
            self.assertEqual([p['chunk_index'] for p in parts], [0, 1, 2])
            self.assertEqual([p['pair_byte_range'] for p in parts], [[0, 3072], [3072, 6144], [6144, 8192]])
            self.assertTrue(all(p['sources'] == parts[0]['sources'] for p in parts))
        self.assertEqual(sum(len(g['nodes']) for g in one['rank_graphs']), 104)
        self.config['send_slots_per_rank'] = 2
        two = build_plan(self.config)
        validate_plan(one)  # Later fixture edits must not mutate an earlier plan.
        self.assertEqual(one['input']['send_slots_per_rank'], 1)
        self.assertEqual(one['rank_graphs'], two['rank_graphs'])
        self.assertEqual(one['transfers'], two['transfers'])
        for field, value in [('chunk_index', 1), ('pair_byte_range', [1, 3073]),
                             ('pair_payload_bytes', 8193), ('payload_bytes', 3071)]:
            bad = deepcopy(one); bad['transfers'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): validate_plan(bad)
        bad = deepcopy(one); bad['transfers'][2]['sources'] = deepcopy(bad['transfers'][2]['sources'])
        bad['transfers'][2]['sources'][0]['expert_ids'] = [7]
        with self.assertRaises(ValueError): validate_plan(bad)

    def test_chunk_size_boundary(self):
        self.config['hidden_dim'] = 2**31
        self.config['chunk_bytes'] = 2**32 - 1
        plan = build_plan(self.config)
        self.assertEqual([t['payload_bytes'] for t in plan['transfers'][:2]], [2**32 - 1, 1])
        self.assertEqual(plan['transfers'][1]['pair_byte_range'], [2**32 - 1, 2**32])
        self.config['chunk_bytes'] = 2**32
        with self.assertRaises(ValueError): build_plan(self.config)


if __name__ == '__main__':
    unittest.main()
