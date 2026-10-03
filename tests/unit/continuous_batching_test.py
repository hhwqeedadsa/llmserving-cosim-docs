"""Real native Scheduler progress and KV admission with supplied boundary times."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.configured_service import ConfiguredService
from moe_ub_cosim.plan import build_plan, validate_config, validate_plan

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    path = ROOT/'fixtures/m7_continuous_batching'
    return json.loads((path/'service.json').read_text()), json.loads((path/'layer.json').read_text())


def decide(service, path, now, complete=False):
    completed = [dict(kind='WAVE_DONE', graph_id=w['graph_id'], wave_id=w['wave_id'], finished_ns=now)
                 for w in service.waves.values()] if complete else []
    return service.decide(dict(schema_version='cosim/1', type='DECIDE', run_id=service.config['run_id'],
        decision_id=service.decision_id, now_ns=now, completed=completed,
        available_rank_ids=service.ranks if complete or not service.waves else []), path)


class ContinuousBatchingTest(unittest.TestCase):
    def test_abc_routes_progress_and_release(self):
        config, layer = fixture(); service = ConfiguredService(config, layer)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); (path/'graphs').mkdir()
            decide(service, path, 0)
            before = deepcopy(service.snapshot())
            self.assertFalse(decide(service, path, 1000)['submissions'])
            self.assertEqual(before, service.snapshot())
            for now in (10000,20000,30000): response = decide(service, path, now, True)
            self.assertTrue(response['service_finished'])
            self.assertEqual([[t['request_id'] for t in w['tokens']] for w in service.history],
                             [['201','202'],['201','203'],['201','203']])
            self.assertEqual([[t['token_index'] for t in w['tokens']] for w in service.history],
                             [[2,5],[3,8],[4,9]])
            self.assertEqual([[t['expert_ids'] for t in w['tokens']] for w in service.history],
                             [[[2],[2]],[[0],[2]],[[2],[1]]])
            self.assertEqual([r.num_tokens_reached for r in service.requests], [6,7,11])
            self.assertEqual([len(r.itl) for r in service.requests], [3,1,2])
            self.assertEqual([e['generated_tokens'] for e in service.batch_events if e['event']=='completed'],[2,2,2])
            self.assertEqual([e['request_id'] for e in service.memory_events if e['event']=='release'],[202,201,203])
            self.assertEqual([r.recent_end for r in service.requests],[30000,10000,30000])
            self.assertTrue(all(r['kv_used_bytes']==0 for r in service.memory_snapshot()))

    def test_kv_wait_does_not_stop_resident_progress(self):
        config, layer = fixture(); config['memory']['ranks'][0]['device_bytes'] -= 512
        service = ConfiguredService(config, layer)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); (path/'graphs').mkdir()
            decide(service,path,0); decide(service,path,1000)
            for now in (10000,20000,30000,40000,50000): decide(service,path,now,True)
            self.assertEqual([[t['request_id'] for t in w['tokens']] for w in service.history],
                             [['201','202'],['201'],['201'],['203'],['203']])
            waits=[e for e in service.memory_events if e['event']=='wait']
            self.assertTrue(any(e['request_id']==203 and e['reason']=='kv_capacity' for e in waits))
            admit=next(e for e in service.memory_events if e['event']=='admit' and e['request_id']==203)
            self.assertEqual(admit['time_ns'],30000)
            self.assertTrue(all(s.num_preemptions==s.recompute_tokens==0 for s in service.schedulers))

    def test_resident_not_selected_retains_kv(self):
        config, layer = fixture()
        config['scheduling']=layer['batch_limits']=dict(max_num_seqs=3,max_num_batched_tokens=1)
        layer['tokens']=layer['tokens'][:1]
        config['requests'][2]['arrival_ns']=0
        config['memory']['ranks'][0]['device_bytes'] += 512  # Four blocks for A(1), B(1), C(2).
        service=ConfiguredService(config,layer)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory); (path/'graphs').mkdir(); decide(service,path,0)
            self.assertEqual(service.memory_snapshot()[0]['request_blocks'],{'201':1,'202':1,'203':2})
            self.assertEqual([r.num_computed_tokens for r in service.requests],[3,5,8])
            for now in range(10000,70000,10000): decide(service,path,now,True)
            self.assertEqual([[t['request_id'] for t in w['tokens']] for w in service.history],
                             [['201'],['201'],['201'],['202'],['203'],['203']])
            self.assertEqual(len(service.completed_ids),3)

    def test_reference_shapes_and_no_cross_request_dedup(self):
        _,layer=fixture(); p=build_plan(layer)
        self.assertEqual([t['payload_bytes'] for t in p['transfers']],[128,128])
        self.assertTrue(all(len(t['sources'])==2 for t in p['transfers']))
        self.assertEqual(p['expert_input_counts'],[0,2])
        shapes=p['compute_inputs']['ranks']
        self.assertEqual([r['context_tokens'] for r in shapes[0]['requests']],[3,6])
        self.assertEqual(shapes[1]['expert_inputs'],[dict(expert_id=2,input_tokens=2)])
        for mutate in [lambda p:p['compute_inputs']['ranks'][1]['expert_inputs'][0].update(input_tokens=1),
                       lambda p:p['transfers'][0]['sources'].pop(),
                       lambda p:p['rank_graphs'][0]['nodes'][0].update(duration_ns=5000)]:
            bad=deepcopy(p); mutate(bad)
            with self.assertRaises(ValueError): validate_plan(bad)

    def test_reject_invalid_budget_route_and_schema(self):
        config, layer=fixture()
        for mutate in [lambda c,l:c['scheduling'].update(max_num_seqs=0),
                       lambda c,l:l['batch_limits'].update(max_num_batched_tokens=0),
                       lambda c,l:c.update(schema_version='moe-ub-configured-service/1'),
                       lambda c,l:c['memory']['ranks'][0].update(communication_buffer_bytes=1),
                       lambda c,l:c['requests'][2].update(prompt_tokens=30),
                       lambda c,l:l['compute_dtypes'].update(compute='bfloat16')]:
            c,l=deepcopy(config),deepcopy(layer); mutate(c,l)
            with self.assertRaises(ValueError): ConfiguredService(c,l)
        for mutate in [lambda l:l['tokens'][1].update(request_id='201',token_index=3),
                       lambda l:l['tokens'].append(dict(l['tokens'][0],request_id='204')),
                       lambda l:l.update(role='P')]:
            l=deepcopy(layer); mutate(l)
            with self.assertRaises(ValueError): validate_config(l)


if __name__=='__main__': unittest.main()
