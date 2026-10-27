"""Independent contribution partitions and source-level progress counterexamples."""
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import unittest

from moe_ub_cosim.hybrid_deepep_plan import compile_plan
from moe_ub_cosim.hybrid_deepep_progress import HybridDispatchProgress

ROOT = Path(__file__).resolve().parents[2]


def example():
    return json.loads((ROOT/'examples/hybrid_logical/input.json').read_text())


class HybridTests(unittest.TestCase):
    def test_independent_expert_expansion(self):
        config = example()
        # Enumerate expert contribution rows first, then compare partitions.
        for reduction, expanded in ((True, True), (True, False), (False, True), (False, False)):
            config['execution'].update(allow_multiple_reduction=reduction, expanded_layout=expanded)
            plan = compile_plan(config)
            atoms = {(src, t, k, expert//4) for src, tokens in enumerate(config['calls'][0]['tokens'])
                     for t, choices in enumerate(tokens) for k, expert in enumerate(choices)}
            demand = {(s, t, gpu//2) for s, t, k, gpu in atoms}
            self.assertEqual(demand, {(d['source'], d['token'], d['target_domain']) for d in plan['dispatch']})
            dispatch = {d['id']: d for d in plan['dispatch']}
            actual_atoms = set()
            actual_partitions = set()
            for row in plan['combine']:
                d = dispatch[row['dispatch']]
                s, t = d['source'], d['token']
                actual_atoms.update((s, t, k, config['calls'][0]['tokens'][s][t][k]//4) for k in row['topk_positions'])
                actual_partitions.add((s, t, tuple(row['topk_positions'])))
                self.assertEqual(row['source'] % 2, row['target'] % 2)
                self.assertIsNone(row['schedule'])
            self.assertEqual(atoms, actual_atoms)
            expected = set()
            for s, t, k, gpu in atoms:
                selected = tuple(sorted(k2 for s2, t2, k2, g2 in atoms if s2 == s and t2 == t and
                    (g2//2 == gpu//2 if reduction else k2 == k if expanded else g2 == gpu)))
                expected.add((s, t, selected))
            self.assertEqual(expected, actual_partitions)
            forward_atoms = set()
            for row in plan['forwards']:
                d = dispatch[row['dispatch']]
                self.assertIsNone(row['gpu_slot'])
                self.assertEqual(row['source']//2, row['target']//2)
                for k in row['topk_positions']:
                    forward_atoms.add((d['source'], d['token'], k, row['target']))
            self.assertEqual(atoms, forward_atoms)
            for row in plan['count_blocks']:
                owner = row['target']//2
                targets = range(2*owner, 2*owner+2) if row['kind'] == 'rank' else range(8*owner, 8*owner+8)
                tokens = config['calls'][0]['tokens'][row['source']]
                expected = [-1-sum(any((e//4 if row['kind'] == 'rank' else e) == target for e in choices)
                                   for choices in tokens) for target in targets]
                self.assertEqual(row['values'], expected)
                self.assertEqual(row['operand_bytes'], 4*len(expected))
                self.assertEqual(row['path'] == 'gin_self', row['source'] == row['target'])
            self.assertEqual(sum(c['path'] == 'gin_self' for c in plan['count_blocks']), 8)

    def test_small_universe_channel_and_rail_mapping(self):
        config = example()
        config['model'].update(experts=8, top_k=2)
        config['execution'].update(num_sms=2, scaleout_warps=2)
        for src in range(4):
            for choices in combinations(range(8), 2):
                config['calls'][0]['tokens'] = [[] for _ in range(4)]
                config['calls'][0]['tokens'][src] = [list(choices)]*5
                plan = compile_plan(config)
                for data in plan['dispatch']:
                    self.assertEqual(data['ingress'], 2*(choices[0]//4)+src%2 if data['target_domain'] == choices[0]//4
                                     else 2*(choices[1]//4)+src%2)
                    self.assertEqual(data['channel'], data['token'] % 4)
                    # Tokens 0 and 4 share channel 0; all others start slot 0.
                    self.assertEqual(data['domain_channel_slot'], int(data['token'] == 4))

    def test_finish_even_zero_or_exact_multiple(self):
        config = example()
        for count in (0, 5, 6, 7, 12, 13):
            config['calls'][0]['tokens'] = [[[8, 9, 10, 12]]*count, [], [], []]
            plan = compile_plan(config)
            c = next(c for c in plan['channels'] if c['id'] == 'h0.s0.d1.c0')
            self.assertEqual(c['publications'], [dict(tail=i, finished=False) for i in range(6, count+1, 6)]+
                             [dict(tail=count, finished=True)])
            self.assertEqual(len(plan['channels']), 8)
            self.assertEqual(sum(c['publications'][-1]['finished'] for c in plan['channels']), 8)

    def test_selected_empty_source_blocks_other_ready_source(self):
        runtime = HybridDispatchProgress(compile_plan(example()))
        actions = json.loads((ROOT/'examples/hybrid_logical/prefix.json').read_text())['actions']
        results = [runtime.apply(a) for a in actions]
        self.assertEqual(results[0]['source_domain'], 1)
        self.assertEqual(results[0]['status'], 'waiting_for_selected_peer')
        self.assertEqual(results[8]['status'], 'waiting_for_selected_peer')
        self.assertEqual(results[10]['status'], 'finished_empty_peer')
        self.assertEqual(results[11]['admitted'], [f'h0.s0.t{i}.d1' for i in range(6)])
        snapshot = runtime.snapshot()
        target = next(f for f in snapshot['forwarders'] if f['ingress'] == 2)
        self.assertEqual(target['consumed'], [6, 0])
        self.assertFalse(target['admission_complete'])
        self.assertFalse(snapshot['all_forwarder_admissions_complete'])
        self.assertFalse(snapshot['scope']['final_gpu_slots_resolved'])
        # The seventh data arrival alone cannot advance the last item.
        runtime.apply(dict(kind='data_visible', transfer='h0.s0.t6.d1'))
        waiting = runtime.apply(dict(kind='forward_step', call=0, ingress=2, channel=0))
        self.assertEqual(waiting['status'], 'waiting_for_selected_peer')
        runtime.apply(dict(kind='tail_visible', channel='h0.s0.d1.c0', publication=1))
        self.assertEqual(runtime.apply(dict(kind='forward_step', call=0, ingress=2, channel=0))['admitted'], ['h0.s0.t6.d1'])

    def test_cached_tail_does_not_refresh_when_selected_has_data(self):
        config = example()
        config['calls'][0]['tokens'] = [[[8, 9, 10, 12]]*6, [], [[8, 9, 10, 12]]*6, []]
        runtime = HybridDispatchProgress(compile_plan(config))
        for src in (0, 2):
            for i in range(6):
                runtime.apply(dict(kind='data_visible', transfer=f'h0.s{src}.t{i}.d1'))
            runtime.apply(dict(kind='tail_visible', channel=f'h0.s{src}.d1.c0', publication=0))
        step = dict(kind='forward_step', call=0, ingress=2, channel=0)
        self.assertEqual(runtime.apply(step)['source_domain'], 1)
        # New finish exists in memory, but source 0 has cached data: no refresh.
        runtime.apply(dict(kind='tail_visible', channel='h0.s2.d1.c0', publication=1))
        self.assertEqual(runtime.apply(step)['source_domain'], 0)
        target = next(f for f in runtime.snapshot()['forwarders'] if f['ingress'] == 2)
        self.assertEqual(target['cached_finish'], [False, False])
        self.assertEqual(runtime.apply(step)['status'], 'finished_empty_peer')
        self.assertEqual(runtime.apply(step)['status'], 'waiting_for_selected_peer')

    def test_visibility_and_publication_rejections_are_atomic(self):
        runtime = HybridDispatchProgress(compile_plan(example()))
        before = runtime.snapshot()
        for action in (dict(kind='tail_visible', channel='h0.s0.d1.c0', publication=0),
                       dict(kind='tail_visible', channel='h0.s0.d1.c0', publication=1),
                       dict(kind='data_visible', transfer=[]), dict(kind='made_up'),
                       dict(kind='forward_step', call=True, ingress=2, channel=0)):
            with self.assertRaises(ValueError): runtime.apply(action)
            self.assertEqual(before, runtime.snapshot())
        action = dict(kind='tail_visible', channel='h0.s2.d1.c0', publication=0)
        runtime.apply(action)
        with self.assertRaises(ValueError): runtime.apply(action)

    def test_three_domain_round_robin_waits_for_both_selected_empty_peers(self):
        config = example()
        config['model'].update(experts=12, top_k=2)
        config['deployment'] = dict(domains=[dict(name=f's{i}', ranks=[2*i, 2*i+1]) for i in range(3)],
                                    rails=[dict(name=f'r{i}', ranks=[i, i+2, i+4]) for i in range(2)])
        config['calls'][0]['tokens'] = [[[8, 9]]*7, [], [], [], [], []]
        runtime = HybridDispatchProgress(compile_plan(config))
        for i in range(6): runtime.apply(dict(kind='data_visible', transfer=f'h0.s0.t{i}.d2'))
        runtime.apply(dict(kind='tail_visible', channel='h0.s0.d2.c0', publication=0))
        step = dict(kind='forward_step', call=0, ingress=4, channel=0)
        first = runtime.apply(step)
        self.assertEqual((first['source_domain'], first['status']), (1, 'waiting_for_selected_peer'))
        runtime.apply(dict(kind='tail_visible', channel='h0.s2.d2.c0', publication=0))
        self.assertEqual(runtime.apply(step)['status'], 'finished_empty_peer')
        second = runtime.apply(step)
        self.assertEqual((second['source_domain'], second['status']), (2, 'waiting_for_selected_peer'))
        runtime.apply(dict(kind='tail_visible', channel='h0.s4.d2.c0', publication=0))
        self.assertEqual(runtime.apply(step)['status'], 'finished_empty_peer')
        self.assertEqual(runtime.apply(step)['admitted'], [f'h0.s0.t{i}.d2' for i in range(6)])

    def test_unsupported_inputs_and_limits(self):
        config = example()
        cases = []
        for key, value in (('mode', 'direct'), ('num_sms', True), ('max_tokens_per_rank', 17), ('expanded_layout', 1)):
            c = deepcopy(config); c['execution'][key] = value; cases.append(c)
        for key, value in (('max_rows', 1), ('max_output_bytes', 1), ('max_input_bytes', False)):
            c = deepcopy(config); c['limits'][key] = value; cases.append(c)
        c = deepcopy(config); c['deployment']['rails'][0]['ranks'] = [0, 3]; cases.append(c)
        c = deepcopy(config); c['deployment']['domains'][0]['ranks'][0] = False; cases.append(c)
        c = deepcopy(config); c['calls'][0]['tokens'][0][0] = [8, 8, 10, 12]; cases.append(c)
        for c in cases:
            with self.assertRaises(ValueError): compile_plan(c)


if __name__ == '__main__':
    unittest.main()
