"""Original block/context identity and full capacity for a finite execution."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.segment import select,validate_segment
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
