"""Dynamic arrival boundaries with the locked Scheduler and real graph export."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.service import Service, validate_service

ROOT = Path(__file__).resolve().parents[2]


class DynamicServiceTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'fixtures/m3_dynamic_arrivals/two_groups.json').read_text())
        self.layer = json.loads((ROOT / 'fixtures/m2_single_layer/layer.json').read_text())

    def message(self, service, now, idle, completed=None):
        return dict(schema_version='cosim/1', type='DECIDE', run_id=service.config['run_id'],
            decision_id=service.decision_id, now_ns=now, completed=completed or [], available_rank_ids=idle)

    def test_busy_arrival_preserves_batch_and_idle_group_starts_at_arrival(self):
        service = Service(self.config, self.layer)
        self.assertTrue(all(r["latest_completion_ns"] is None for r in service.initial))
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary); (out / 'graphs').mkdir()
            first = service.decide(self.message(service, 0, list(range(8))), out)
            self.assertEqual(first['next_wakeup_ns'], 5200)
            batch = service.schedulers[0].inflight[0]
            state = service.snapshot()
            queued = service.decide(self.message(service, 5200, [4,5,6,7]), out)
            self.assertEqual(queued['submissions'], [])
            self.assertEqual(queued['next_wakeup_ns'], 5500)
            self.assertEqual(service.snapshot(), state)
            self.assertIs(service.schedulers[0].inflight[0], batch)
            self.assertEqual([r.id for r in service.pending[0]], [201])
            self.assertEqual(service.scheduler_calls, 4)
            # A locally completed member of a busy group cannot be advertised as idle.
            with self.assertRaisesRegex(ValueError, 'availability'):
                service.decide(self.message(service, 5500, [0,4,5,6,7]), out)
            response = service.decide(self.message(service, 5500, [4,5,6,7]), out)
            self.assertEqual(response['submissions'][0]['rank_ids'], [4,5,6,7])
            self.assertTrue(response['input_exhausted'])
            self.assertFalse(response['service_finished'])
            self.assertIs(service.schedulers[0].inflight[0], batch)
            plan = json.loads((out / 'graphs/g1/plan.json').read_text())
            self.assertEqual(plan['input']['group_id'], 'ep1')
            self.assertEqual(plan['ready_ns'], 5500)
            self.assertTrue(all(t['src_rank'] in [4,5,6,7] and t['dst_rank'] in [4,5,6,7] for t in plan['transfers']))
            prior = json.loads((out / 'graphs/g0/plan.json').read_text())
            self.assertTrue({t['transfer_id'] for t in prior['transfers']}.isdisjoint(t['transfer_id'] for t in plan['transfers']))

    def test_idle_jump_and_group_completion_order(self):
        config = deepcopy(self.config)
        config['requests'] = [r for r in config['requests'] if r['request_id'] < 200 or r['request_id'] >= 300]
        for r in config['requests']: r['arrival_ns'] = 9000
        service = Service(config, self.layer)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary); (out / 'graphs').mkdir()
            first = service.decide(self.message(service, 0, list(range(8))), out)
            self.assertEqual(first['submissions'], [])
            self.assertEqual(first['next_wakeup_ns'], 9000)
            service.decide(self.message(service, 9000, list(range(8))), out)
            completions = [dict(kind='WAVE_DONE', graph_id=f'g{i}', wave_id=f'D.w{i}', finished_ns=35000) for i in range(2)]
            before = service.snapshot()
            with self.assertRaisesRegex(ValueError, 'Unordered'):
                service.decide(self.message(service, 35000, list(range(8)), completions[::-1]), out)
            self.assertEqual(before, service.snapshot())
            response = service.decide(self.message(service, 35000, list(range(8)), completions), out)
            self.assertEqual(len(response['submissions']), 2)
            self.assertTrue(all(w['finished_ns'] == 35000 for w in service.history[:2]))

    def test_explicit_interleaved_members_not_inferred_from_ep_size(self):
        config = deepcopy(self.config)
        mapping = {0:0,1:2,2:4,3:6,4:1,5:3,6:5,7:7}
        for g in config['groups']:
            for key in ('rank_ids', 'rank_to_ub_node', 'expert_placement'):
                g[key] = [mapping[r] for r in g[key]]
        for r in config['requests']: r['rank_id'] = mapping[r['rank_id']]
        validate_service(config, self.layer)
        service = Service(config, self.layer)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary); (out / 'graphs').mkdir()
            response = service.decide(self.message(service, 0, list(range(8))), out)
            self.assertEqual(response['submissions'][0]['rank_ids'], [0,2,4,6])

    def test_reject_overlap_bad_mapping_and_invalid_request(self):
        cases = []
        c = deepcopy(self.config); c['groups'][1] = deepcopy(c['groups'][0]); cases.append(c)
        c = deepcopy(self.config); c['groups'][1]['rank_to_ub_node'][0] = 0; cases.append(c)
        c = deepcopy(self.config); c['groups'][1]['rank_ids'][0] = True; cases.append(c)
        c = deepcopy(self.config); c['groups'][1]['expert_placement'][0] = 0; cases.append(c)
        c = deepcopy(self.config); c['requests'][-1]['arrival_ns'] = True; cases.append(c)
        c = deepcopy(self.config); c['requests'][-1]['decode_routes'] = []; cases.append(c)
        for c in cases:
            with self.subTest(config=c), self.assertRaises(ValueError):
                validate_service(c, self.layer)


if __name__ == '__main__':
    unittest.main()
