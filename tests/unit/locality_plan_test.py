from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.plan import build_plan, validate_plan, RunIdentities
from moe_ub_cosim.locality import LOCAL_PROFILE
from moe_ub_cosim.sequence import build_sequence

FIXTURE = Path(__file__).resolve().parents[2]/'fixtures/m6_locality'


class LocalityTest(unittest.TestCase):
    def fixture(self, name='mixed-layer'):
        return json.loads((FIXTURE/(name+'.json')).read_text())

    def test_all_classes_and_rank_dedup(self):
        p = build_plan(self.fixture())
        self.assertEqual(Counter(c['locality'] for c in p['contributions']), dict(same_rank=4,same_chip=2,cross_chip=5))
        self.assertEqual(p['expert_input_counts'], [2,5,3,2])
        local = [t for t in p['transfers'] if t['transport_profile_id'] == LOCAL_PROFILE]
        fabric = [t for t in p['transfers'] if t not in local]
        self.assertEqual((len(local),len(fabric)), (4,10))
        self.assertEqual(sum(t['payload_bytes'] for t in local),512)
        self.assertEqual(sum(t['payload_bytes'] for t in fabric),1280)
        self.assertTrue(all('ub_task_id' not in t for t in local))

    def test_no_inferred_chip_and_no_local_chunking(self):
        c=self.fixture('local-layer');c['chunk_bytes']=1
        p=build_plan(c)
        self.assertEqual(len(p['transfers']),2)
        c['schema_version']='moe-ub-layer-fixture/1';del c['deployment']
        old=build_plan(c)
        self.assertEqual(len(old['transfers']),256)
        self.assertTrue(all('ub_task_id' in t for t in old['transfers']))

    def test_identity_allocation_separates_ub_tasks(self):
        ids=RunIdentities();p=build_plan(self.fixture(),ids)
        self.assertEqual((ids.transfer,ids.task,ids.tag),(2**32+114,110,114))
        self.assertEqual([t['ub_task_id'] for t in p['transfers'] if 'ub_task_id' in t],list(range(100,110)))

    def test_reject_physical_input(self):
        mutations=[lambda c:c['deployment']['ranks'][1].update(die_id='die-2'),
            lambda c:c['deployment']['ranks'][2].update(chip_id='server0.chip0'),
            lambda c:c['deployment']['ranks'][0].update(rank_id=5),
            lambda c:c['deployment']['local_prepare_ns_per_vector'].update(dispatch=-1),
            lambda c:c['deployment']['local_prepare_ns_per_vector'].update(dispatch=2**63-1),
            lambda c:c['deployment'].update(network_scope='all_ranks'),
            lambda c:c.update(schema_version='moe-ub-layer-fixture/1')]
        for mutate in mutations:
            c=self.fixture();mutate(c)
            with self.subTest(c=c), self.assertRaises(ValueError):build_plan(c)

    def test_reject_lost_dependency_and_false_locality(self):
        p=build_plan(self.fixture())
        a=deepcopy(p);a['contributions'][1]['locality']='cross_chip'
        with self.assertRaises(ValueError):validate_plan(a)
        a=deepcopy(p);t=next(t for t in a['transfers'] if t['transport_profile_id']==LOCAL_PROFILE)
        g=next(g for g in a['rank_graphs'] if g['rank_id']==t['src_rank'])
        next(n for n in g['nodes'] if n['node_id']==t['send_node_id'])['deps']=[]
        with self.assertRaises(ValueError):validate_plan(a)
        a=deepcopy(p);a['schema_version']='moe-ub-execution-plan/1'
        with self.assertRaises(ValueError):validate_plan(a)

    def test_fixed_steps_require_static_placement(self):
        first=self.fixture();second=deepcopy(first);second.update(graph_id='g-next',wave_id='D.next')
        for token in second['tokens']:token['token_index']+=1
        seq=dict(schema_version='moe-ub-sequence-fixture/1',steps=[first,second])
        self.assertEqual(len(build_sequence(seq)),2)
        second['deployment']['ranks'][0]['chip_id']='server0.chip1'
        with self.assertRaises(ValueError):build_sequence(seq)


if __name__=='__main__':unittest.main()
