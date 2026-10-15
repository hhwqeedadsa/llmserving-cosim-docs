"""Independent enumeration and workload-accounting checks, without network execution."""
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_capacity import estimate, execute

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/scaleout_capacity/1024-decode.json'


class CapacityTest(unittest.TestCase):
    def setUp(self):
        self.c = json.loads(EXAMPLE.read_text())

    def test_rank_dedup_against_exhaustive_expert_selection(self):
        c = self.c
        c['model'].update(routed_experts=8, top_k=3)
        c['parallel']['ep_size'] = 4
        choices = list(combinations(range(8), 3))
        expected = sum(len({e // 2 for e in choice} - {0}) for choice in choices) / len(choices)
        r = estimate(c)
        self.assertAlmostEqual(r['workload']['expected_remote_destination_ranks_per_token'], expected)
        self.assertLess(expected, r['workload']['expected_remote_expert_copies_per_token'])

    def test_all_local_and_all_experts(self):
        self.c['parallel']['ep_size'] = 1
        r = estimate(self.c)
        self.assertEqual(r['logical_remote_rank_volume']['per_expert_copy']['all_moe_legs_bytes_per_window'], 0)
        self.assertEqual(r['parallel']['potential_directed_peer_pairs'], 0)
        self.c['parallel']['ep_size'] = 64
        self.c['model']['top_k'] = 256
        self.assertEqual(estimate(self.c)['workload']['expected_remote_destination_ranks_per_token'], 63)

    def test_batch_phase_accumulation_and_memory(self):
        a = estimate(self.c)
        self.c['workload']['sequences_per_rank'] = 4
        b = estimate(self.c)
        volume = lambda r: r['logical_remote_rank_volume']['per_expert_copy']['all_moe_legs_bytes_per_window']
        self.assertEqual(volume(b), 4 * volume(a))
        self.c['workload'].update(phase='train', sequence_length=128, gradient_accumulation=8)
        t = estimate(self.c)
        self.assertEqual(volume(t), volume(b) * 128 * 2 * 8)
        self.assertEqual(t['workload']['sequences_per_window'], 1024 * 4 * 8)
        self.assertEqual(t['parallel']['expert_replica_groups'], 16)
        self.assertEqual(a['memory']['locked_total_array_bytes'], 6455040000)
        self.assertFalse(a['network_executed'])

    def test_prefill_uses_query_tokens_and_oversubscription_is_separate(self):
        a = estimate(self.c)
        self.c['workload'].update(phase='prefill', sequence_length=128)
        self.c['fabric']['uplink_rate_bps'] //= 2
        b = estimate(self.c)
        self.assertEqual(b['workload']['tokens_per_ep_group_microbatch'], 64 * 128)
        for template in a['logical_remote_rank_volume']:
            self.assertEqual(b['logical_remote_rank_volume'][template]['all_moe_legs_bytes_per_window'],
                             128 * a['logical_remote_rank_volume'][template]['all_moe_legs_bytes_per_window'])
        self.assertEqual(b['fabric']['down_to_up_capacity_ratio'], 2)

    def test_reject_ambiguous_or_impossible_inputs(self):
        for section, key, value in [('parallel', 'ep_size', 512), ('parallel', 'ep_size', 3),
                ('parallel', 'tp_size', 2), ('model', 'top_k', 257), ('model', 'top_k', True),
                ('workload', 'sequence_length', 128), ('workload', 'gradient_accumulation', 2),
                ('fabric', 'leaf_port_capacity', 63), ('fabric', 'leaf_uplinks', 31),
                ('fabric', 'nics_per_gpu', 2), ('workload', 'phase', 'training')]:
            with self.subTest(section=section, key=key, value=value):
                c = deepcopy(self.c)
                c[section][key] = value
                with self.assertRaises(ValueError):
                    estimate(c)

    def test_new_output_and_saved_effective_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'estimate'
            execute(EXAMPLE, out)
            self.assertEqual((out / 'config.json').read_bytes(), EXAMPLE.read_bytes())
            self.assertEqual(json.loads((out / 'capacity.json').read_text())['state'], 'estimate_only')
            with self.assertRaises(FileExistsError):
                execute(EXAMPLE, out)


if __name__ == '__main__':
    unittest.main()
