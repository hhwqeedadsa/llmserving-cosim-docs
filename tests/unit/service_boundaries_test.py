"""Sparse EP participation, real request progress and absent-work graphs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan
from moe_ub_cosim.service import Service

ROOT = Path(__file__).resolve().parents[2]


class ServiceBoundariesTest(unittest.TestCase):
    def setUp(self):
        self.layer = json.loads((ROOT / 'fixtures/m2_single_layer/layer.json').read_text())
        self.config = json.loads((ROOT / 'fixtures/m3_service/service.json').read_text())

    def test_remote_experts_without_local_tokens_and_empty_rank(self):
        self.layer['tokens'] = self.layer['tokens'][:1]
        plan = build_plan(self.layer)
        self.assertEqual(plan['expert_input_counts'], [0, 1, 1, 0])
        self.assertEqual(len(plan['transfers']), 4)
        self.assertEqual(sum(t['payload_bytes'] for t in plan['transfers']), 32768)
        self.assertEqual([b['request_ids'] for b in plan['instance_batches']], [['A'], [], [], []])
        self.assertEqual(plan['rank_graphs'][3]['nodes'], [])
        for rank in (1, 2):
            graph = plan['rank_graphs'][rank]
            self.assertIsNone(graph['phase_nodes']['prepare'])
            self.assertIsNone(graph['phase_nodes']['finish'])
            self.assertIsNone(graph['phase_nodes']['next_layer'])
            self.assertEqual([n['kind'] for n in graph['nodes']], ['RECV', 'COMP', 'COMP', 'SEND'])
            self.assertEqual(graph['nodes'][0]['deps'], [])
            self.assertEqual([n['duration_ns'] for n in graph['nodes'] if n['kind'] == 'COMP'], [4000, 1000])
        # Omitting a required transfer cannot turn remote expert work into local work.
        broken = deepcopy(plan); broken['transfers'] = []
        with self.assertRaisesRegex(ValueError, 'Token routes'): validate_plan(broken)

    def test_local_only_and_no_padding_compute(self):
        self.layer['tokens'] = self.layer['tokens'][:1]
        self.layer['tokens'][0]['expert_ids'] = [0, 1]
        plan = build_plan(self.layer)
        self.assertEqual(plan['transfers'], [])
        self.assertEqual(plan['expert_input_counts'], [2, 0, 0, 0])
        self.assertEqual(len(plan['rank_graphs'][0]['nodes']), 5)
        self.assertTrue(all(not g['nodes'] and set(g['phase_nodes'].values()) == {None}
                            for g in plan['rank_graphs'][1:]))

    def test_sparse_arrival_waits_for_whole_group_and_unequal_steps(self):
        self.config['requests'] = self.config['requests'][:2]
        self.config['requests'][1]['arrival_ns'] = 5500
        self.config['requests'][1]['decode_routes'] = [[0, 5]]
        service = Service(self.config, self.layer)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp); (path / 'graphs').mkdir()
            def decide(now, idle, done=None):
                return service.decide(dict(schema_version='cosim/1', type='DECIDE',
                    run_id=self.config['run_id'], decision_id=service.decision_id, now_ns=now,
                    completed=done or [], available_rank_ids=idle), path)
            first = decide(0, [0,1,2,3])
            self.assertEqual(first['submissions'][0]['rank_ids'], [0,1,2,3])
            self.assertEqual(service.scheduler_calls, 1)
            self.assertTrue(all(service.schedulers[r].is_request_empty() for r in (1,2,3)))
            current = service.schedulers[0].inflight[0]
            waiting = decide(5500, [])
            self.assertTrue(waiting['input_exhausted'])
            self.assertFalse(waiting['service_finished'])
            self.assertEqual(waiting['submissions'], [])
            self.assertIs(service.schedulers[0].inflight[0], current)
            for step, now in enumerate((25000, 50000, 75000)):
                done = [dict(kind='WAVE_DONE', graph_id=f'g{step}', wave_id=f'D.w{step}', finished_ns=now)]
                response = decide(now, [0,1,2,3], done)
                if step < 2:
                    plan = json.loads((path / f'graphs/g{step+1}/plan.json').read_text())
                    self.assertEqual([t['src_rank'] for t in plan['input']['tokens']], [0,1] if step == 0 else [0])
            self.assertTrue(response['service_finished'])
            self.assertEqual(service.scheduler_calls, 4)
            self.assertEqual([len(s.done) for s in service.schedulers], [1,1,0,0])
            self.assertEqual([len(r.itl) for r in service.requests], [3,1])
            self.assertEqual(service.requests[1].itl, [50000-5500])

    def test_empty_service_needs_no_wave_or_request(self):
        self.config['requests'] = []
        service = Service(self.config, self.layer)
        with tempfile.TemporaryDirectory() as tmp:
            response = service.decide(dict(schema_version='cosim/1', type='DECIDE',
                run_id=self.config['run_id'], decision_id=0, now_ns=0, completed=[],
                available_rank_ids=[0,1,2,3]), Path(tmp))
        self.assertTrue(response['service_finished'] and response['input_exhausted'])
        self.assertEqual(response['submissions'], [])
        self.assertEqual(service.scheduler_calls, 0)


if __name__ == '__main__':
    unittest.main()
