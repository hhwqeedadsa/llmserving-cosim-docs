"""Segment boundaries, independent groups and a single capacity ledger."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.composed_service import ComposedService
from moe_ub_cosim.multilayer import validate_multilayer

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    p=ROOT/'fixtures/m8_composed_service'
    return json.loads((p/'service.json').read_text()),json.loads((p/'layers.json').read_text())


def decide(service,path,now,finished=()):
    done=[dict(kind='WAVE_DONE',graph_id=service.waves[g]['graph_id'],
        wave_id=service.waves[g]['wave_id'],finished_ns=now) for g in sorted(finished)]
    idle=[r for r in service.ranks if service.owners[r] not in service.waves or service.owners[r] in finished]
    return service.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=service.config['run_id'],
        decision_id=service.decision_id,now_ns=now,completed=done,available_rank_ids=idle),path)


class ComposedServiceTest(unittest.TestCase):
    def test_full_model_inventory_and_segment_buffers(self):
        c,l=fixture();s=ComposedService(c,l)
        self.assertEqual(s.ranks,[2,7,12,20,25,30])
        for name,evidence in s.evidence['groups'].items():
            audit=evidence['model']
            self.assertEqual(audit['executed_layer_ids'],[1,2,3])
            self.assertEqual(audit['unexecuted_layer_ids'],[0])
            self.assertEqual([r['weight_bytes'] for r in audit['ranks']],[3811840,3811840,4991488])
            self.assertEqual([r['kv_bytes_per_token'] for r in audit['ranks']],[2048]*3)
            self.assertEqual([r['kv_capacity_blocks'] for r in audit['ranks']],[5,2,2])
            self.assertEqual(audit['communication_buffer_minimum_bytes'],36864)
        c['groups'][0]['memory']['ranks'][0]['communication_buffer_bytes']=20000
        with self.assertRaisesRegex(ValueError,'complete layer-segment bound'):ComposedService(c,l)

    def test_groups_finish_independently_and_token_advances_once(self):
        c,l=fixture();s=ComposedService(c,l)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'graphs').mkdir()
            decide(s,path,0);decide(s,path,600)
            before=deepcopy(s.snapshot());decide(s,path,1000)
            self.assertEqual(s.snapshot(),before)
            decide(s,path,10000,['ep1'])
            self.assertEqual(s.waves['ep0']['graph_id'],'g0')
            decide(s,path,15000,['ep1'])
            self.assertNotIn('ep1',s.waves)
            self.assertTrue(all(not r.itl for r in s.requests if r.id<400))
            for time in (20000,30000,40000):response=decide(s,path,time,['ep0'])
            self.assertTrue(response['service_finished'])
            a=[w for w in s.history if w['group_id']=='ep0']
            self.assertEqual([[t['request_id'] for t in w['tokens']] for w in a],
                [['201','202','204'],['201','203','204'],['201','203']])
            self.assertEqual(len(s.consumed),36)
            self.assertEqual(sum(len(r.itl) for r in s.requests),12)
            self.assertEqual(len(s.completed_ids),7)
            self.assertTrue(all(r['kv_used_bytes']==0 for r in s.memory_snapshot()))
            p=json.loads((path/'graphs/g0/plan.json').read_text())
            self.assertEqual(p['schema_version'],'moe-ub-multilayer-plan/2')
            self.assertEqual([child['input']['expert_placement'] for child in p['layers']],
                             [[2,7,12,12],[12,2,7,12],[7,12,2,12]])
            self.assertTrue(any('ub_task_id' not in t for t in p['transfers']))
            self.assertEqual(len({t['comm_tag'] for t in p['transfers']}),len(p['transfers']))
            for graph in p['rank_graphs']:
                for previous,current in zip(graph['layer_phases'],graph['layer_phases'][1:]):
                    self.assertEqual(current['entry_deps'],previous['terminal_nodes'])
            validate_multilayer(p)
            bad=deepcopy(p);bad['rank_graphs'][0]['layer_phases'][1]['entry_deps']=[]
            with self.assertRaisesRegex(ValueError,'composition mismatch'):validate_multilayer(bad)
            bad=deepcopy(p);bad['layers'][1]['input']['tokens'][0]['token_index']+=1
            with self.assertRaises(ValueError):validate_multilayer(bad)

    def test_kv_wait_in_composed_path(self):
        c,l=fixture();c['groups']=c['groups'][:1];l['groups']=l['groups'][:1]
        c['requests']=[r for r in c['requests'] if r['request_id']<400]
        c['groups'][0]['memory']['ranks'][0]['device_bytes']-=8192
        s=ComposedService(c,l)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'graphs').mkdir()
            decide(s,path,0);decide(s,path,1000)
            for time in (10000,20000,30000,40000,50000):response=decide(s,path,time,['ep0'])
            self.assertTrue(response['service_finished'])
            self.assertEqual([[t['request_id'] for t in w['tokens']] for w in s.history],
                [['201','202','204'],['201','204'],['201'],['203'],['203']])
            self.assertTrue(any(e.get('reason')=='kv_capacity' for e in s.memory_events))
            self.assertEqual(len(s.consumed),24)

    def test_each_group_has_its_own_token_budget(self):
        c,l=fixture();c['groups'][1]['scheduling']['max_num_batched_tokens']=1
        for layer in l['groups'][1]['layers']:layer['batch_limits']['max_num_batched_tokens']=1
        s=ComposedService(c,l)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'graphs').mkdir();decide(s,path,0);decide(s,path,600)
            self.assertEqual([t['request_id'] for t in s.history[0]['tokens']],['201','202','204'])
            self.assertEqual([t['request_id'] for t in s.history[1]['tokens']],['401','404'])
            memory=next(r for r in s.memory_snapshot() if r['rank_id']==20)
            self.assertEqual(memory['request_blocks'],{'401':1,'402':2})

    def test_invalid_combinations_rejected_before_scheduling(self):
        c,l=fixture()
        mutations=[
            lambda c,l:c['groups'][1].update(group_id='ep0'),
            lambda c,l:l['groups'][1]['layers'][0]['rank_to_ub_node'].__setitem__(0,0),
            lambda c,l:l['groups'][0]['layers'][1].update(layer_id=3),
            lambda c,l:l['groups'][0]['layers'][1]['expert_placement'].__setitem__(0,2),
            lambda c,l:c['requests'][0]['decode_routes'][0].pop('2'),
            lambda c,l:c['requests'][0]['decode_routes'][0].update({'4':[0,1]}),
            lambda c,l:c['requests'][0].update(rank_id=999),
            lambda c,l:c['requests'][2].update(prompt_tokens=40),
            lambda c,l:c['groups'][1]['model_spec'].update(max_position_embeddings=129),
        ]
        for mutate in mutations:
            a,b=deepcopy(c),deepcopy(l);mutate(a,b)
            with self.subTest(mutation=mutate),self.assertRaises((ValueError,KeyError)):ComposedService(a,b)
        a,b=deepcopy(c),deepcopy(l)
        for layer in b['groups'][1]['layers']:layer['deployment']['ranks'][0]['die_id']='die-2'
        with self.assertRaisesRegex(ValueError,'global die'):ComposedService(a,b)


if __name__=='__main__':unittest.main()
