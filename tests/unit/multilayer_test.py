"""Layer identity, local dependency boundaries, route continuity and atomic IDs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.data_inputs import file_evidence, load_inputs
from moe_ub_cosim.data_service import DataService
from moe_ub_cosim.multilayer import build_multilayer, validate_multilayer
from moe_ub_cosim.plan import RunIdentities

ROOT=Path(__file__).resolve().parents[2]


class MultilayerTest(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'fixtures/m5_multilayer/service.json').read_text())
        self.layer=json.loads((ROOT/'fixtures/m5_multilayer/layer.json').read_text())

    def plan(self):
        service=DataService(self.config,self.layer)
        candidate=deepcopy(self.layer)
        candidate.update(graph_id='g0',wave_id='D.w0',ready_ns=0,tokens=[])
        for row in self.config['requests']:
            r=service.config['requests'][row['rank_id']]
            candidate['tokens'].append(dict(request_id=str(r['request_id']),src_rank=r['rank_id'],phase='decode',
                token_index=r['prompt_tokens']+r['generated_tokens']-1,layer_id=6,expert_ids=r['decode_routes'][0]))
        return service,service.make_plan(candidate)

    def test_compose_distinct_routes_and_local_boundaries(self):
        service,p=self.plan();validate_multilayer(p)
        self.assertEqual(len(service.consumed),12)
        self.assertEqual([len(x['transfers']) for x in p['layers']],[14,12,0])
        self.assertEqual([op['layer_id'] for op in p['operations']],[6,6,7,7,8,8])
        for graph in p['rank_graphs']:
            previous=[]
            for phase in graph['layer_phases']:
                self.assertEqual(phase['entry_deps'],previous)
                nodes={n['node_id']:n for n in graph['nodes'] if n['layer_id']==phase['layer_id']}
                roots=[n for n in nodes.values() if not set(n['deps']) & set(nodes)]
                self.assertTrue(roots)
                for root in roots:self.assertEqual(root['deps'],previous)
                self.assertIsNone(phase['phase_nodes']['next_layer'])
                previous=phase['terminal_nodes']
            self.assertFalse(any(n['name']=='next-layer' for n in graph['nodes']))

    def test_wrong_composition_and_layer_identity_rejected(self):
        _,p=self.plan()
        changes=[lambda q:q['rank_graphs'][0]['nodes'][-1].update(deps=[]),
            lambda q:q['rank_graphs'][0]['layer_phases'][1].update(entry_deps=[]),
            lambda q:q['transfers'][0].update(send_node_id=999),
            lambda q:q['layers'].reverse(),
            lambda q:q['layers'][1]['input'].update(graph_id='different'),
            lambda q:q['layers'][1]['input']['tokens'][0].update(token_index=100),
            lambda q:q['layers'][1]['operations'][0].update(operation_id=q['layers'][0]['operations'][0]['operation_id'])]
        for change in changes:
            q=deepcopy(p);change(q)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_multilayer(q)

    def test_run_ids_atomic_when_multilayer_exhausts(self):
        _,p=self.plan();configs=[q['input'] for q in p['layers']]
        ids=RunIdentities(tag=499999990);before=deepcopy(ids.__dict__)
        with self.assertRaisesRegex(ValueError,'exhausted'):build_multilayer(configs,ids,[[]]*3)
        self.assertEqual(ids.__dict__,before)

    def test_bad_layer_profiles_are_not_silently_replaced(self):
        changes=[lambda c:c['layer_profiles'].reverse(),lambda c:c['layer_profiles'].pop(1),
            lambda c:c['layer_profiles'][1]['compute_profile_ns'].update(next_layer=1),
            lambda c:c['layer_profiles'][1]['compute_profile_ns'].update(expert_per_input=0),
            lambda c:c.update(compute={'kind':'h100_fp16_dense_reference'}),
            lambda c:c['memory'].update(communication_buffer_bytes=131072)]
        for change in changes:
            c=deepcopy(self.config);change(c)
            with self.subTest(config=c),self.assertRaises(ValueError):load_inputs(c,self.layer)

    def test_routes_require_every_request_step_and_layer(self):
        original=[json.loads(line) for line in (ROOT/self.config['routes']['file']['path']).read_text().splitlines()]
        for change in ('missing','duplicate','wrong-layer','wrong-position'):
            records=deepcopy(original)
            if change=='missing':records.pop(5)
            elif change=='duplicate':records.append(deepcopy(records[5]));records[-1]['source_index']=99999
            elif change=='wrong-layer':records[5]['layer_id']=9
            else:records[5]['source_token_index']+=10
            with tempfile.TemporaryDirectory() as name:
                p=Path(name)/'routes.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in records))
                c=deepcopy(self.config);c['routes']['file']=file_evidence(p)
                with self.subTest(change=change),self.assertRaises(ValueError):load_inputs(c,self.layer)

    def test_empty_rank_keeps_previous_boundary_across_empty_layer(self):
        _,p=self.plan();configs=[deepcopy(q['input']) for q in p['layers']]
        # Rank 3 has expert work in layer 6, none in 7, then receives work in 8.
        for c,experts in zip(configs,([6,7],[0,1],[6,7])):
            c['tokens']=c['tokens'][:1];c['tokens'][0]['expert_ids']=experts
        q=build_multilayer(configs,RunIdentities(),[[]]*3)
        graph=q['rank_graphs'][3]
        self.assertEqual(graph['layer_phases'][1]['phase_nodes'],dict.fromkeys(('prepare','expert','local_merge','finish','next_layer')))
        self.assertEqual(graph['layer_phases'][0]['terminal_nodes'],graph['layer_phases'][2]['entry_deps'])
        self.assertFalse(any(n['layer_id']==7 for n in graph['nodes']))


if __name__=='__main__':unittest.main()
