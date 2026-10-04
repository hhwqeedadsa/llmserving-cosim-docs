"""Full Decode coverage, reference cost domain and unchanged scheduler ownership."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from composed_service_test import decide
from moe_ub_cosim.full_model import build_full_model, model_structure, validate_full_model
from moe_ub_cosim.full_model_service import FullModelService, FullModelEvidenceService
from moe_ub_cosim.mla_capacity import audit_execution_capacity
from moe_ub_cosim.plan import RunIdentities

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    p=ROOT/'fixtures/m10_full_model'
    return json.loads((p/'service.json').read_text()),json.loads((p/'layers.json').read_text())


def build(c,l):
    g=c['groups'][0];ls=l['groups'][0]['layers']
    return build_full_model(ls,RunIdentities(),[[] for _ in ls],g['model_spec'],g['full_model_profile'])


class FullModelTest(unittest.TestCase):
    def test_structure_and_empty_source(self):
        c,l=fixture();p=build(c,l)
        self.assertEqual(p['executed_layer_ids'],[0,1,2,3]);self.assertEqual(p['communication_layer_ids'],[1,2,3])
        source=p['rank_graphs'][0]
        # Four head stages + eight dense stages + ten outer stages per MoE.
        # Quant pack is checked separately inside each CANN child.
        self.assertEqual(len(source['model_stages']),4+8+3*10)
        costs={(s['layer_id'],s['stage']):s for s in source['model_stages']}
        self.assertEqual(costs[0,'mla_attention']['duration_ns'],15)
        self.assertEqual(costs[0,'dense_ffn']['duration_ns'],23)
        self.assertEqual(costs[1,'shared_expert']['duration_ns'],500)
        for lid in (1,2,3):
            shared=costs[lid,'shared_expert'];router=costs[lid,'router'];join=costs[lid,'shared_routed_add']
            self.assertEqual(shared['entry_deps'],router['entry_deps'])
            self.assertIn(shared['node_id'],join['entry_deps'])
            self.assertTrue(set(source['layer_phases'][lid-1]['terminal_nodes']) <= set(join['entry_deps']))
        for g in p['rank_graphs'][1:]:
            self.assertTrue(all(s['node_id'] is None and s['duration_ns']==0 for s in g['model_stages']))
        self.assertTrue(any(n['kind']=='COMP' for n in p['rank_graphs'][2]['nodes']))
        self.assertNotIn('communication_buffers',p)
        validate_full_model(p)

    def test_context_and_batch_cost(self):
        c,l=fixture()
        for row in l['groups'][0]['layers']:
            token=deepcopy(row['tokens'][0]);token.update(request_id='second',token_index=9);row['tokens'].append(token)
        p=build(c,l);stages=p['rank_graphs'][0]['model_stages']
        self.assertTrue(all(s['context_tokens']==[3,10] and s['batch_size']==2 for s in stages))
        self.assertEqual(next(s for s in stages if s['stage']=='mla_attention')['duration_ns'],65)
        self.assertEqual(next(s for s in stages if s['stage']=='shared_expert')['duration_ns'],1000)

    def test_absent_shared_has_no_ffn_or_add_cost(self):
        c,l=fixture();g=c['groups'][0]
        for block,cost in zip(g['model_spec']['layers'],g['full_model_profile']['layers']):
            if block['kind']=='moe':
                block.update(shared_experts=0,shared_intermediate_size=0)
                cost['per_token_ns'].update(shared_expert=0,shared_routed_add=0)
        g['full_model_profile']['model_structure']=model_structure(g['model_spec'])
        p=build(c,l)
        self.assertTrue(all(s['node_id'] is None for graph in p['rank_graphs'] for s in graph['model_stages']
                            if s['stage'] in ('shared_expert','shared_routed_add')))
        g['full_model_profile']['layers'][1]['per_token_ns']['shared_routed_add']=1
        with self.assertRaises(ValueError):build(c,l)

    def test_sixty_one_blocks_without_target_scale_claim(self):
        c,l=fixture();g=c['groups'][0];m=g['model_spec'];profile=g['full_model_profile'];ls=l['groups'][0]['layers']
        dense,moe=deepcopy(m['layers'][0]),deepcopy(m['layers'][1]);dc,mc=deepcopy(profile['layers'][0]),deepcopy(profile['layers'][1]);template=deepcopy(ls[0])
        m['layers']=[];profile['layers']=[];ls.clear()
        for i in range(61):
            m['layers'].append(dict(deepcopy(dense if i<3 else moe),layer_id=i))
            profile['layers'].append(dict(deepcopy(dc if i<3 else mc),layer_id=i))
            if i>=3:
                row=deepcopy(template);row['layer_id']=i;row['tokens'][0]['layer_id']=i;ls.append(row)
        profile['model_structure']=model_structure(m);p=build(c,l)
        self.assertEqual(p['executed_layer_ids'],list(range(61)))
        self.assertEqual(len(p['layers']),58);self.assertLessEqual(len(p['transfers']),4096)
        self.assertEqual(sum(s['stage']=='mla_attention' for s in p['rank_graphs'][0]['model_stages']),61)
        self.assertEqual(sum(s['stage']=='shared_expert' for s in p['rank_graphs'][0]['model_stages']),58)

    def test_reject_cost_domain_missing_work_and_tampering(self):
        c,l=fixture()
        mutations=[lambda c,l:c['groups'][0]['full_model_profile']['per_token_ns'].pop('sampling'),
            lambda c,l:c['groups'][0]['full_model_profile']['layers'][0]['per_token_ns'].update(dense_ffn=0),
            lambda c,l:c['groups'][0]['model_spec']['attention'].update(kv_lora_rank=64),
            lambda c,l:l['groups'][0]['layers'].pop(),
            lambda c,l:l['groups'][0]['layers'][0]['compute_profile_ns'].update(next_layer=5),
            lambda c,l:c['groups'][0]['full_model_profile']['layers'][0].update(attention_per_context_token_ns=2**63-1),
            lambda c,l:l['groups'][0]['layers'][1]['tokens'][0].update(token_index=4)]
        for change in mutations:
            a,b=deepcopy(c),deepcopy(l);change(a,b)
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):FullModelService(a,b)
        p=build(c,l)
        for change in (lambda p:p['rank_graphs'][0]['nodes'].pop(),
                       lambda p:p['rank_graphs'][0]['nodes'][-1].update(deps=[]),
                       lambda p:p['rank_graphs'][0]['model_stages'].pop()):
            q=deepcopy(p);change(q)
            with self.assertRaisesRegex(ValueError,'composition mismatch'):validate_full_model(q)

    def test_service_and_evidence_reuse_native_pool(self):
        c,l=fixture();s=FullModelService(c,l)
        self.assertEqual(s.deployment()['schema_version'],'moe-ub-service-deployment/5')
        self.assertTrue(all(r['kv_capacity_blocks']==8 and r['weight_bytes']==12786248 for r in s.audit_by_rank.values()))
        for e in s.evidence['groups'].values():self.assertEqual(e['model']['unexecuted_layer_ids'],[])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);(path/'graphs').mkdir();decide(s,path,0)
            self.assertTrue(all(q.num_tokens_reached==next(r["prompt_tokens"]+r["generated_tokens"] for r in c["requests"] if r["request_id"]==q.id) for scheduler in s.schedulers for q in scheduler.running))
            plan=json.loads((path/'graphs/g0/plan.json').read_text());self.assertEqual(plan['schema_version'],'moe-ub-full-model-plan/1')
            decide(s,path,600)
            self.assertTrue(all(q.num_tokens_reached==next(r["prompt_tokens"]+r["generated_tokens"] for r in c["requests"] if r["request_id"]==q.id) for scheduler in s.schedulers for q in scheduler.running))
        e=json.loads((ROOT/'fixtures/m10_full_model/evidence.json').read_text())
        observed=FullModelEvidenceService(e,l)
        self.assertEqual(observed.manifest_fields()['compute_scope'],'full_model_decode_reference')
        with self.assertRaisesRegex(ValueError,'shared-expert'):
            audit_execution_capacity(c['groups'][0]['model_spec'],c['groups'][0]['memory'],l['groups'][0]['layers'][0])


if __name__=='__main__':unittest.main()
