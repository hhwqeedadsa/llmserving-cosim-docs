"""Original block/context identity and full capacity for a finite execution."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
import tempfile

from moe_ub_cosim.segment import select,select_source,validate_segment
from moe_ub_cosim.full_model_service import FullModelService
from moe_ub_cosim.full_model import build_full_model,validate_full_model
from moe_ub_cosim.plan import RunIdentities
from moe_ub_cosim.resources import actual_usage,full_usage

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    root=ROOT/'fixtures/m19_a3_resources'
    return json.loads((root/'service.json').read_text()),json.loads((root/'layers.json').read_text())


def build(service,layers):
    configs=deepcopy(layers['groups'][0]['layers']);group=service['groups'][0]
    for c in configs:
        c['tokens']=[dict(request_id=str(r['request_id']),src_rank=r['rank_id'],phase='decode',
            token_index=r['prompt_tokens']+r['generated_tokens']-1,layer_id=c['layer_id'],
            expert_ids=r['decode_routes'][0][str(c['layer_id'])]) for r in service['requests']]
    p=build_full_model(configs,RunIdentities(),[[] for _ in configs],group['model_spec'],group['full_model_profile'],
                       segment=service['execution']['segment'])
    return p,configs


class SegmentTest(unittest.TestCase):
    def test_verified_source_step_and_identity(self):
        from moe_ub_cosim.plan_io import file_sha256
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);service,layers=fixture();service['requests']=[]
            model=service['groups'][0]['model_spec']
            manifest=json.loads((ROOT/'fixtures/m9_evidence/manifest.json').read_text())
            manifest['model'].update(name=model['name'],hidden_dim=model['hidden_size'],num_layers=4)
            manifest['layers']=[dict(layer_id=i,kind='moe' if i else 'dense',num_experts=6 if i else 0,top_k=2 if i else 0) for i in range(4)]
            raw=[{str(i):None if i==0 else [[0,1]]*3 for i in range(4)},
                 {str(i):None if i==0 else [[2,3]] for i in range(4)},
                 {str(i):None if i==0 else [[4,5]] for i in range(4)}]
            (path/'raw.json').write_text(json.dumps(raw))
            ref=lambda p:dict(path=str(p),sha256=file_sha256(p))
            manifest['files']=[dict(file_id='a',original_path='synthetic/a.json',file=ref(path/'raw.json'),format='token_layer_map_v1')]
            (path/'manifest.json').write_text(json.dumps(manifest))
            source=dict(schema_version='moe-ub-evidence-service/1',run_id=service['run_id'],service=service,
                route_manifest=ref(path/'manifest.json'),compute_profiles=[],requests=[dict(request_id=203,rank_id=2,
                arrival_ns=0,file_id='a',first_decode_entry=1,decode_steps=2,target_total_tokens=None)])
            s,l,e=select_source(source,layers,2,2,1);scope=s['execution']['segment']
            self.assertEqual(scope['decode_step'],1);self.assertEqual(scope['requests'][0]['generated_tokens_at_trace_start'],1)
            self.assertEqual(scope['requests'][0]['token_index'],4)
            self.assertEqual(s['requests'][0]['decode_routes'],[{'2':[4,5],'3':[4,5]}])
            self.assertEqual([(r['source_entry'],r['source_layer_id']) for r in e['records']],[(2,2),(2,3)])
            self.assertFalse(e['eos_inferred']);front=FullModelService(s,l)
            proof=dict(schema_version='moe-ub-segment-preparation/1',passed=True,segment=scope,route_evidence=e)
            front.bind_segment_source(proof)
            self.assertEqual(front.manifest_fields()['routing_source'],'synthetic_test')
            from composed_service_test import decide
            run=path/'run';(run/'graphs').mkdir(parents=True)
            decide(front,run,0)
            graph=json.loads((run/'graphs/g0/plan.json').read_text())
            validate_full_model(graph)
            self.assertEqual(graph['route_source'],'synthetic_test')
            self.assertEqual(graph['input_provenance']['route_records'],e['records'])
            bad=deepcopy(proof);bad['route_evidence']['records'][0]['expert_ids']=[0,1]
            with self.assertRaisesRegex(ValueError,'route/context'):front.bind_segment_source(bad)
            with self.assertRaisesRegex(ValueError,'outside'):select_source(source,layers,2,2,2)
            (path/'raw.json').write_text('[]')
            with self.assertRaisesRegex(ValueError,'SHA256'):select_source(source,layers,2,2,1)

    def test_original_layers_work_and_full_capacity(self):
        s,l=fixture();original=deepcopy((s,l));old=FullModelService(s,l)
        service,layers=select(s,l,2,2,0);new=FullModelService(service,layers)
        self.assertEqual((s,l),original)
        self.assertEqual(service['groups'],s['groups'])
        self.assertEqual({r:a['weight_bytes'] for r,a in old.audit_by_rank.items()},
                         {r:a['weight_bytes'] for r,a in new.audit_by_rank.items()})
        p,configs=build(service,layers)
        self.assertEqual(p['executed_layer_ids'],[2,3]);self.assertEqual(p['communication_layer_ids'],[2,3])
        self.assertEqual(actual_usage(p),full_usage(configs,p['model_spec'],segment=p['segment']))
        for graph in p['rank_graphs']:
            self.assertEqual({r['layer_id'] for r in graph['model_stages']},{2,3})
            self.assertFalse(any(r['stage'] in ('embedding','final_norm','lm_head','sampling') for r in graph['model_stages']))
        self.assertEqual(new.manifest_fields()['latency_scope'],'declared_block_entry_to_segment_completion')
        self.assertEqual(new.evidence['groups']['ep0']['model']['unexecuted_layer_ids'],[0,1])
        validate_full_model(p)

    def test_selected_decode_step_preserves_context_and_route(self):
        s,l=fixture();s['requests']=s['requests'][:1]
        service,layers=select(s,l,1,3,1);p,_=build(service,layers)
        old=s['requests'][0];new=service['requests'][0]
        self.assertEqual(new['generated_tokens'],old['generated_tokens']+1)
        self.assertEqual(new['decode_routes'],old['decode_routes'][1:2])
        self.assertEqual(p['layers'][0]['input']['tokens'][0]['token_index'],old['prompt_tokens']+old['generated_tokens'])
        FullModelService(service,layers)

    def test_invalid_scope_and_missing_source_rejected(self):
        s,l=fixture()
        for first,count,step in [(0,2,0),(2,3,0),(1,0,0),(1,3,1),(True,1,0)]:
            with self.assertRaises((ValueError,KeyError)):select(s,l,first,count,step)
        service,layers=select(s,l,2,2,0);scope=service['execution']['segment']
        for mutate in (lambda x:x['initial_state'].update(network='snapshot'),
                       lambda x:x['initial_state'].update(simulation_ns=False),
                       lambda x:x['requests'][0].update(token_index=99)):
            invalid=deepcopy(scope);mutate(invalid)
            with self.assertRaises(ValueError):validate_segment(invalid,s['groups'][0]['model_spec'])
        p,_=build(service,layers);p['rank_graphs'][0]['nodes'][0]['duration_ns']+=1
        with self.assertRaises(ValueError):validate_full_model(p)


if __name__=='__main__':unittest.main()
