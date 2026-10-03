"""Independent small-model arithmetic and final-input KV reservation lifecycle."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.configured_service import ConfiguredService

ROOT = Path(__file__).resolve().parents[2]


def fixture(name='gqa'):
    directory = ROOT / 'fixtures/m6_service_model'
    return tuple(json.loads((directory / f'{name}-{kind}.json').read_text()) for kind in ('service', 'layer'))


class ModelCapacityTest(unittest.TestCase):
    def test_actual_expert_placement_and_full_inventory(self):
        service = ConfiguredService(*fixture())
        audit = service.evidence['model']
        self.assertEqual([r['weight_bytes'] for r in audit['ranks']], [255872, 280448, 231296])
        self.assertEqual([r['kv_capacity_blocks'] for r in audit['ranks']], [3, 3, 2])
        self.assertEqual([r['kv_bytes_per_token'] for r in audit['ranks']], [384]*3)
        self.assertEqual([r['kv_block_bytes'] for r in audit['ranks']], [1536]*3)
        self.assertEqual([r['layers'][1]['local_experts'] for r in audit['ranks']], [1, 3, 1])
        self.assertEqual([r['layers'][2]['local_experts'] for r in audit['ranks']], [2, 0, 1])
        self.assertEqual(audit['executed_layer_ids'], [1])
        self.assertEqual(audit['unexecuted_layer_ids'], [0, 2])
        self.assertTrue(all(r['layers'][0]['kind'] == 'dense' for r in audit['ranks']))
        for rank, scheduler in service.scheduler_by_rank.items():
            self.assertEqual(scheduler.memory.get_weight(), service.audit_by_rank[rank]['weight_bytes'])
            self.assertIs(scheduler.kv, scheduler.memory.kv)
            self.assertEqual(scheduler.memory.get_kv(2), 768)

    def test_second_model_precision_and_tied_weights(self):
        service = ConfiguredService(*fixture('mha'))
        ranks = service.evidence['model']['ranks']
        self.assertEqual([r['weight_bytes'] for r in ranks], [74496, 49920])
        self.assertEqual([r['kv_block_bytes'] for r in ranks], [512, 512])
        self.assertEqual([r['kv_capacity_blocks'] for r in ranks], [2, 2])
        self.assertEqual(ranks[0]['shared_components'], dict(embedding=8192, final_norm=128, lm_head=0))

    def test_reservation_progress_release_and_fifo(self):
        config, layer = fixture()
        service = ConfiguredService(config, layer)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary); (output/'graphs').mkdir()
            def decision(now, completed):
                return service.decide(dict(schema_version='cosim/1', type='DECIDE', run_id=config['run_id'],
                    decision_id=service.decision_id, now_ns=now, completed=completed,
                    available_rank_ids=[] if service.waves and not completed else service.ranks), output)
            response = decision(0, [])
            self.assertFalse(response['service_finished'])
            first = service.memory_events[0]
            self.assertEqual((first['num_computed_tokens'], first['num_tokens_reached']), (5, 6))
            self.assertEqual(first['reserved_kv_tokens'], 9)
            self.assertEqual(first['ranks'][0]['request_blocks'], {'101': 3})
            decision(1000, [])
            self.assertEqual([r.id for r in service.pending[2]], [103])
            for step in range(6):
                now = (step+1)*20000
                wave = service.waves['ep0']
                response = decision(now, [dict(kind='WAVE_DONE', graph_id=wave['graph_id'],
                                               wave_id=wave['wave_id'], finished_ns=now)])
                if step < 3:
                    self.assertEqual(len(service.schedulers[0].kv.req_to_blocks[101]), 3)
                if step == 3:
                    self.assertEqual(service.schedulers[0].kv.req_to_blocks.keys(), {103})
            self.assertTrue(response['service_finished'])
        admits = [e for e in service.memory_events if e['event'] == 'admit']
        releases = [e for e in service.memory_events if e['event'] == 'release']
        self.assertEqual([e['request_id'] for e in admits], [101, 102, 103])
        self.assertEqual([e['request_id'] for e in releases], [102, 101, 103])
        self.assertEqual(admits[-1]['time_ns'], 80000)
        self.assertEqual(releases[1]['ranks'][0]['kv_used_bytes'], 0)
        self.assertTrue(all(r['kv_used_bytes'] == 0 for r in service.memory_snapshot()))
        self.assertTrue(all(r.num_preemptions == 0 for r in service.requests))

    def test_final_capacity_and_block_rounding(self):
        config, layer = fixture('mha')
        # The initial context fits in one block; the last input needs two.
        config['memory']['ranks'][0]['device_bytes'] -= 512
        with self.assertRaisesRegex(ValueError, 'final KV demand 2 blocks'):
            ConfiguredService(config, layer)
        config['requests'][0]['decode_routes'].pop()
        service = ConfiguredService(config, layer)
        self.assertEqual(service.requirements[201]['reserved_kv_tokens'], 8)
        self.assertEqual(service.requirements[201]['reserved_kv_blocks'], 1)
        # Total output is 9; a one-token iteration budget is still sufficient.
        self.assertEqual(service.schedulers[0].max_num_batched_tokens, 1)

    def test_reject_invalid_model_budget_or_scope(self):
        original, layer = fixture()
        mutations = [
            lambda c: c['model_spec'].update(architecture='MLA'),
            lambda c: c['model_spec']['layers'][0].update(kind='attention'),
            lambda c: c['model_spec']['dtypes'].update(weights='int4'),
            lambda c: c['model_spec']['dtypes'].update(kv=[]),
            lambda c: c['model_spec']['attention'].update(num_kv_heads=3),
            lambda c: c['model_spec'].update(tp=2),
            lambda c: c['model_spec']['layers'][1]['expert_placement'].__setitem__(0, 7),
            lambda c: c['model_spec']['layers'][2]['expert_placement'].__setitem__(0, 99),
            lambda c: c['model_spec']['layers'][1].update(num_experts=4),
            lambda c: c['model_spec']['layers'][1].update(shared_experts=1, shared_intermediate_size=32),
            lambda c: c['model_spec']['layers'][0].update(layer_id=1),
            lambda c: c['memory'].update(capacity_scope='whole_chip'),
            lambda c: c['memory']['ranks'].pop(),
            lambda c: c['memory']['ranks'][1].update(rank_id=2),
            lambda c: c['memory']['ranks'][0].update(communication_buffer_bytes=1),
            lambda c: c['memory']['ranks'][0].update(device_bytes=255872),
            lambda c: c['memory'].update(block_size=0),
            lambda c: c['memory'].update(max_kv_blocks_per_rank=2),
            lambda c: c['scheduling'].update(max_num_seqs=2),
            lambda c: c['scheduling'].update(max_num_batched_tokens=0),
            lambda c: c['requests'][0].update(prompt_tokens=250),
            lambda c: c['requests'][0].update(rank_id=1),
            lambda c: c['requests'][0].update(request_id=102),
            lambda c: c['requests'][0]['decode_routes'].__setitem__(0, [0, 1]),
            lambda c: c['model_spec'].update(vocab_size=2**63-1),
        ]
        for index, mutate in enumerate(mutations):
            config = deepcopy(original); mutate(config)
            with self.subTest(index=index), self.assertRaises(ValueError):
                ConfiguredService(config, layer)


if __name__ == '__main__':
    unittest.main()
