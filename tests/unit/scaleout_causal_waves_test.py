from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_causal_waves import compile_waves

ROOT = Path(__file__).resolve().parents[2]


class CausalWaveTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT/'examples/scaleout_causal_waves'
        self.options = json.loads((self.root/'plan.json').read_text())
        self.waves = [json.loads((self.root/p).read_text()) for p in self.options['waves']]
        self.protocol = json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology = dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i, kind='gpu') for i in range(4)]+[dict(id=4, kind='tor', plane=0)],
            links=[dict(src=i, dst=4, src_port=1, dst_port=i+1, rate_bps=100000000000, delay_ns=1000) for i in range(4)])

    def compile(self, waves=None, options=None):
        return compile_waves(self.waves if waves is None else waves,
                             self.options if options is None else options, self.topology, self.protocol)

    def test_exact_send_receive_and_declared_compute(self):
        p, m = self.compile()
        self.assertEqual(sum(x['kind']=='write' for x in p['work']), 16)
        self.assertEqual(len(m['nodes']), 64)
        nodes = {(x['graph_id'], x['rank'], x['node_id']): x for x in m['nodes']}
        for w in self.waves:
            for rank, tasks in w['rank_nodes'].items():
                for t in tasks:
                    row = nodes[w['graph_id'], int(rank), t['id']]
                    if t['kind']=='RECV':
                        self.assertIn('accepted', [d['event'] for d in row['completion']])
                        self.assertNotIn('acked', [d['event'] for d in row['completion']])
                    else:
                        work = p['work'][row['completion'][0]['work']]
                        if t['kind']=='COMP':
                            self.assertEqual(work['duration_ns'], t['duration_ns'])
                        else:
                            self.assertEqual(work['bytes'], t['message']['bytes'])
        # Original RECV local predecessor is retained alongside byte arrival.
        self.assertEqual(len(nodes['g0.w0', 0, 4]['completion']), 2)

    def test_unreferenced_send_ack_and_group_independence(self):
        p, m = self.compile(); work = {x['id']: x for x in p['work']}
        def ancestors(refs):
            found, todo = set(), [(x['work'], x['event']) for x in refs]
            while todo:
                ref = todo.pop()
                if ref in found:
                    continue
                found.add(ref)
                todo.extend((x['work'], x['event']) for x in work[ref[0]]['after'])
            return found
        nodes = {(x['graph_id'], x['rank'], x['node_id']): x for x in m['nodes']}
        for gi, group in enumerate(((0, 2), (1, 3))):
            first = [x for x in m['nodes'] if x['graph_id']==f'g{gi}.w0']
            next_root = nodes[f'g{gi}.w1', group[0], 1]['completion']
            prior = ancestors(next_root)
            for n in first:
                for d in n['completion']:
                    self.assertIn((d['work'], d['event']), prior)
            other = nodes[f'g{1-gi}.w0', (1, 0)[gi], 1]['completion'][0]
            self.assertNotIn((other['work'], other['event']), prior)

    def test_complete_48_layer_frontend_graph(self):
        from moe_ub_cosim.scaleout_service import make_wave
        c = dict(schema_version='moe-scaleout-service/2', model=dict(hidden_size=64, experts=4, top_k=2, activation_bytes=2, layers=48),
                 workload=dict(batch_per_rank=1, context_tokens=2048, route_seed=11, hotspot_fraction=0),
                 parallel=dict(groups=[[0, 2], [1, 3]]), compute=dict(
                     attention_ns_per_token=100, attention_context_ns_per_token=0, pack_ns_per_token=10,
                     expert_ns_per_contribution=10, merge_ns_per_token=10, embedding_ns_per_token=1,
                     norm_ns_per_token=1, residual_ns_per_token=1, lm_head_ns_per_token=1, sampling_ns_per_token=1))
        waves = [make_wave(c, group, gi, step, 1) for step in range(2) for gi, group in enumerate(c['parallel']['groups'])]
        for w in waves:
            w['rank_nodes'] = {str(k): v for k, v in w['rank_nodes'].items()}
        opts = deepcopy(self.options); opts['limits']['max_work'] = 65536
        p, m = self.compile(waves, opts)
        self.assertEqual(len(m['nodes']), sum(len(ts) for w in waves for ts in w['rank_nodes'].values()))
        self.assertEqual(sum(x['kind']=='write' for x in p['work']), sum(len(w['messages']) for w in waves))
        self.assertEqual(len(m['waves']), 4)
        self.assertTrue(all(len(x['after']) <= 64 for x in p['work']))

    def test_rejects_invalid_graphs_and_bindings(self):
        for case in ('missing_recv', 'mismatch', 'duplicate_message', 'cycle', 'missing_parent',
                     'step_gap', 'overlap', 'different_group', 'missing_binding', 'extra_binding',
                     'duplicate_binding', 'nic', 'work_limit', 'boolean_tag', 'duration', 'unknown_field'):
            waves, opts = deepcopy(self.waves), deepcopy(self.options)
            w = waves[0]
            if case=='missing_recv': w['rank_nodes']['0'].pop(3)
            elif case=='mismatch': w['rank_nodes']['0'][2]['message']['bytes'] += 1
            elif case=='duplicate_message': w['messages'].append(deepcopy(w['messages'][0]))
            elif case=='cycle':
                for rows in w['rank_nodes'].values():
                    rows[0]['deps'] = [4]
            elif case=='missing_parent': w['rank_nodes']['0'][0]['deps'] = [999]
            elif case=='step_gap': waves[2]['decode_step'] = 2
            elif case=='overlap': waves[1]['group_index'] = 9; waves[1]['rank_ids'] = [0, 3]
            elif case=='different_group': waves[2]['rank_ids'] = [0, 3]
            elif case=='missing_binding': opts['streams'].pop()
            elif case=='extra_binding': opts['streams'].append(dict(opts['streams'][0], id=7, dst=3, wire_port=22000))
            elif case=='duplicate_binding': opts['streams'].append(dict(opts['streams'][0], id=7, wire_port=22000))
            elif case=='nic': opts['streams'][0]['source_nic'] = 999
            elif case=='work_limit': opts['limits']['max_work'] = 1
            elif case=='boolean_tag': w['messages'][0]['tag'] = False
            elif case=='duration': w['rank_nodes']['0'][2]['duration_ns'] = 1
            else: w['rank_nodes']['0'][0]['ready_ns'] = 0
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.compile(waves, opts)


if __name__ == '__main__':
    unittest.main()
