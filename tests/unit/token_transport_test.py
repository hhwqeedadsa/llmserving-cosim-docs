"""Token identity, layout sizes, expert work and reduction dependency checks."""
from copy import deepcopy
import json
import unittest

from moe_ub_cosim.phase_completion import structure
from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.token_transport import configuration, payload_sizes, verify_wave


class Routes:
    layers = [3, 4]
    manifest = dict(source=dict(kind='synthetic_unit_routes'))

    def __init__(self, config):
        self.config = config

    def routes(self, group, step, layer):
        return [dict(rank=0, slot=0, experts=[2, 3, 4]),
                dict(rank=0, slot=1, experts=[2, 3, 5]),
                dict(rank=3, slot=0, experts=[6, 7, 2])]

    def owner(self, group, layer, expert):
        placement = self.config.get('route_replay', {}).get('expert_to_group_rank')
        return group[placement[layer][expert] if placement else expert//2]


class TokenTransportChecks(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/astra_stream_service/service.json').read_bytes())
        self.config['model'].update(experts=8, top_k=3)
        self.config['workload']['batch_per_rank'] = 2
        self.config['phase_completion'] = dict(mode='ep_transport_flush/1')
        self.config['token_transport'] = dict(mode='deepep_direct_bf16/1',
            combine='rank_reduced/1', local_reduce_ns_per_extra=100)

    def wave(self, config=None):
        c = self.config if config is None else config
        return make_wave(c, [0, 1, 2, 3], 0, 0, 1, Routes(c))

    def test_default_is_original_activation_contribution_model(self):
        c = deepcopy(self.config)
        del c['token_transport']
        w = self.wave(c)
        verify_wave(w, c)
        self.assertNotIn('token_transport', w)
        self.assertTrue(all(m['bytes'] == m['contributions']*512 and 'transport_tokens' not in m for m in w['messages']))
        self.assertFalse(any(n['name'].endswith('combine_reduce') for tasks in w['rank_nodes'].values() for n in tasks))

    def test_known_BF16_source_layout_sizes(self):
        c = deepcopy(self.config)
        c['model'].update(hidden_size=7168, top_k=8)
        self.assertEqual(payload_sizes(c), dict(dispatch=14464, combine=14400))
        c['token_transport'].update(combine='expanded_per_expert/1', local_reduce_ns_per_extra=0)
        self.assertEqual(payload_sizes(c), dict(dispatch=14464, combine=14336))
        c['model'].update(hidden_size=64, top_k=1)
        self.assertEqual(payload_sizes(c), dict(dispatch=192, combine=128))

    def test_dedup_only_within_each_token_not_between_slots(self):
        w = self.wave()
        verify_wave(w, self.config)
        structure(w, self.config)
        pair = next(m for m in w['messages'] if m['layer'] == 0 and m['phase'] == 'dispatch' and m['src'] == 0 and m['dst'] == 1)
        self.assertEqual((pair['contributions'], pair['transport_tokens'], pair['bytes']), (4, 2, 1152))
        tasks = {n['name']: n for n in w['rank_nodes'][1]}
        self.assertEqual(tasks['L0.experts']['duration_ns'], 500)
        self.assertEqual(tasks['L0.combine_reduce']['duration_ns'], 200)
        tasks = {n['name']: n for n in w['rank_nodes'][3]}
        self.assertEqual(tasks['L0.combine_reduce']['duration_ns'], 100)  # Local source contributes too.

    def test_expanded_combine_preserves_expert_output_copies(self):
        c = deepcopy(self.config)
        c['token_transport'].update(combine='expanded_per_expert/1', local_reduce_ns_per_extra=0)
        w = self.wave(c)
        verify_wave(w, c)
        structure(w, c)
        pair = next(m for m in w['messages'] if m['layer'] == 0 and m['phase'] == 'combine' and m['src'] == 1 and m['dst'] == 0)
        self.assertEqual((pair['contributions'], pair['transport_tokens'], pair['bytes']), (4, 4, 2048))
        self.assertFalse(any(n['name'].endswith('combine_reduce') for tasks in w['rank_nodes'].values() for n in tasks))

    def test_explicit_placement_and_serialized_wave(self):
        c = deepcopy(self.config)
        c['route_replay']['expert_to_group_rank'] = [[1, 1, 0, 0, 3, 3, 2, 2]]*2
        w = self.wave(c)
        verify_wave(json.loads(json.dumps(w)), c)
        structure(json.loads(json.dumps(w)), c)
        self.assertNotEqual(w['messages'], self.wave()['messages'])

    def test_invalid_or_ignored_configuration_rejected(self):
        for kind in ('mode', 'combine', 'boolean', 'negative', 'too-large', 'unused-cost', 'phase', 'transport', 'precision', 'alignment', 'extra-field'):
            c = deepcopy(self.config)
            s = c['token_transport']
            if kind == 'mode': s['mode'] = 'guess'
            if kind == 'combine': s['combine'] = 'guess'
            if kind == 'boolean': s['local_reduce_ns_per_extra'] = True
            if kind == 'negative': s['local_reduce_ns_per_extra'] = -1
            if kind == 'too-large': s['local_reduce_ns_per_extra'] = 10**9+1
            if kind == 'unused-cost': s['combine'] = 'expanded_per_expert/1'
            if kind == 'phase': del c['phase_completion']
            if kind == 'transport': del c['transport']
            if kind == 'precision': c['model']['activation_bytes'] = 1
            if kind == 'alignment': c['model']['hidden_size'] = 65
            if kind == 'extra-field': s['unused'] = 1
            with self.subTest(kind=kind), self.assertRaises(ValueError): configuration(c)

    def test_independent_reader_rejects_bad_bytes_work_and_dependencies(self):
        for kind in ('bytes', 'units', 'contributions', 'expert-work', 'route-count', 'reduction-cost', 'reduction-dep', 'send-dep', 'gate-dep'):
            w = self.wave()
            tasks = {n['name']: n for n in w['rank_nodes'][1]}
            if kind == 'bytes': w['messages'][0]['bytes'] -= 1
            if kind == 'units': w['messages'][0]['transport_tokens'] -= 1
            if kind == 'contributions': w['messages'][0]['contributions'] -= 1
            if kind == 'expert-work': tasks['L0.experts']['duration_ns'] -= 100
            if kind == 'route-count': w['routes'][0]['expert_counts'][2] -= 1
            if kind == 'reduction-cost': tasks['L0.combine_reduce']['duration_ns'] = 0
            if kind == 'reduction-dep': tasks['L0.combine_reduce']['deps'] = []
            if kind == 'send-dep': next(n for n in tasks.values() if n['kind'] == 'SEND' and n['message']['phase'] == 'combine')['deps'] = [tasks['L0.experts']['id']]
            if kind == 'gate-dep': tasks['L0.combine.flush']['deps'].remove(tasks['L0.combine_reduce']['id'])
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                verify_wave(w, self.config)
                structure(w, self.config)


if __name__ == '__main__':
    unittest.main()
