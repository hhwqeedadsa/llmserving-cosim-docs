"""One-request prefill/transfer/decode lifecycle with the real scheduler pools."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.kv_plan import build_kv_plan, validate_kv_plan
from moe_ub_cosim.pd_service import PdService, kv_input, validate_pd
from moe_ub_cosim.plan import RunIdentities, build_plan

ROOT=Path(__file__).resolve().parents[2]


class PdServiceTest(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'fixtures/m4_pd_handoff/service.json').read_text())
        self.layer=json.loads((ROOT/'fixtures/m4_pd_handoff/layer.json').read_text())
        self.model=json.loads((ROOT/'fixtures/m4_pd_handoff/model.json').read_text())

    def test_kv_payload_and_padding_are_distinct(self):
        plan=build_kv_plan(kv_input(self.config,self.model,'g','KV.w',123),RunIdentities())
        validate_kv_plan(plan)
        self.assertEqual(plan['kv_payload_bytes'],4096)
        self.assertEqual([t['payload_bytes'] for t in plan['transfers']],[768,768,512]*2)
        self.assertEqual([t['pair_byte_range'] for t in plan['transfers']],[[0,768],[768,1536],[1536,2048],[2048,2816],[2816,3584],[3584,4096]])
        self.assertEqual([len(g['nodes']) for g in plan['rank_graphs']],[6,0,0,0,6,0,0,0])
        self.config['kv_dtype']='fp8'
        small=build_kv_plan(kv_input(self.config,self.model,'g','KV.w',123))
        self.assertEqual(small['kv_payload_bytes'],2048)
        self.assertEqual(small['input']['model'],plan['input']['model'])

    def test_corrupt_kv_description_rejected(self):
        base=build_kv_plan(kv_input(self.config,self.model,'g','KV.w',0))
        for mutate in (lambda p:p['transfers'].pop(),lambda p:p['transfers'][0].update(kv_component='Q'),
            lambda p:p['transfers'][0].update(payload_bytes=767),lambda p:p['rank_graphs'][4]['nodes'].pop(),
            lambda p:p['transfers'][1].update(comm_tag=p['transfers'][0]['comm_tag'])):
            p=deepcopy(base);mutate(p)
            with self.assertRaises(ValueError):validate_kv_plan(p)

    def test_prefill_real_range_and_memory_lifecycle(self):
        s=PdService(self.config,self.layer)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'graphs').mkdir()
            def decide(now,kind=None):
                done=[] if kind is None else [dict(kind=kind,graph_id=s.history[-1]['graph_id'],wave_id=s.history[-1]['wave_id'],finished_ns=now)]
                return s.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=self.config['run_id'],
                    decision_id=s.decision_id,now_ns=now,completed=done,available_rank_ids=list(range(8))),out)
            first=decide(0);self.assertEqual(first['submissions'][0]['rank_ids'],[0,1,2,3])
            self.assertEqual([t['token_index'] for t in s.history[0]['tokens']],[0,1,2,3])
            self.assertEqual(s.request.ttft,-1)
            self.assertEqual(s.schedulers[0].kv.npu_used_bytes(),16384)
            second=decide(50000,'WAVE_DONE')
            self.assertEqual(second['submissions'][0]['rank_ids'],list(range(8)))
            self.assertEqual((s.request.num_computed_tokens,s.request.num_tokens_reached),(4,5))
            self.assertEqual(s.request.ttft,50000)
            self.assertEqual(s.schedulers[0].kv.npu_used_bytes(),16384)
            self.assertEqual(s.schedulers[4].kv.npu_used_bytes(),16384)
            self.assertEqual(s.schedulers[4].running,[])
            self.assertEqual(s.schedulers[4].inflight,[])
            with self.assertRaisesRegex(ValueError,'incomplete P/D completion'):decide(51000,'WAVE_DONE')
            self.assertEqual(s.state,'KV_INFLIGHT')
            third=decide(60000,'KV_DONE')
            self.assertEqual(third['submissions'][0]['rank_ids'],[4,5,6,7])
            self.assertEqual(s.schedulers[0].kv.npu_used_bytes(),0)
            self.assertEqual(s.schedulers[4].kv.npu_used_bytes(),16384)
            self.assertEqual(s.history[-1]['tokens'][0]['token_index'],4)
            decide(90000,'WAVE_DONE');last=decide(110000,'WAVE_DONE')
            self.assertTrue(last['service_finished'])
            self.assertEqual(s.request.num_tokens_reached,7)
            self.assertEqual(s.request.itl,[40000,20000])
            self.assertEqual(s.scheduler_calls,3)
            self.assertTrue(all(x.kv.is_free() for x in s.schedulers))
            events=[e['event'] for e in s.memory_events]
            self.assertLess(events.index('destination_reserved'),events.index('handoff_submitted'))
            self.assertLess(events.index('handoff_complete'),events.index('source_released'))
            self.assertLess(events.index('source_released'),events.index('decode_submitted'))

    def test_incompatible_layout_or_missing_routes_rejected(self):
        for key,value in (('destination_layout','converted'),('kv_handoff_policy','async'),('kv_dtype','unknown')):
            c=deepcopy(self.config);c[key]=value
            with self.assertRaises(ValueError):validate_pd(c,self.layer)
        c=deepcopy(self.config);c['request']['prefill_routes'].pop()
        with self.assertRaisesRegex(ValueError,'Missing explicit'):validate_pd(c,self.layer)
        c=deepcopy(self.config);c['communication_buffer_bytes']=[1]*8
        with self.assertRaisesRegex(ValueError,'Communication buffer'):validate_pd(c,self.layer)

    def test_capacity_cannot_be_silently_overcommitted(self):
        normal=PdService(self.config,self.layer)
        # One block fits the model, but a one-token block cannot hold the prompt.
        c=deepcopy(self.config);c['block_size']=1
        for rank in (4,5,6,7):
            c['npu_memory_bytes'][rank]=normal.schedulers[rank].memory.weight+c['workspace_bytes'][rank]+c['communication_buffer_bytes'][rank]+1024
        c['request']['prompt_tokens']=5;c['request']['prefill_routes'].append([0,1])
        with self.assertRaisesRegex(ValueError,'total D group capacity'):PdService(c,self.layer)

    def test_future_arrival_does_not_allocate_kv(self):
        self.config['request']['arrival_ns']=9000;s=PdService(self.config,self.layer)
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);(out/'graphs').mkdir()
            response=s.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=self.config['run_id'],decision_id=0,
                now_ns=0,completed=[],available_rank_ids=list(range(8))),out)
            self.assertEqual(response['next_wakeup_ns'],9000)
            self.assertEqual(response['submissions'],[])
            self.assertTrue(all(x.kv.is_free() for x in s.schedulers))


if __name__=='__main__':unittest.main()
