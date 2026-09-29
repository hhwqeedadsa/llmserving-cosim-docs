"""Input contracts, real model capacity and exact compute source domains."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.data_inputs import (ROOT, PROFILE_PATH, audit_model, file_evidence,
                                     import_compute, import_routes, load_inputs)
from moe_ub_cosim.data_service import DataService


class DataInputsTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'fixtures/m4_data_inputs/compute-stage.json').read_text())
        self.layer = json.loads((ROOT/'fixtures/m2_single_layer/layer.json').read_text())
        self.raw = [json.loads(line) for line in (ROOT/self.config['routes']['file']['path']).read_text().splitlines()]

    def routes(self, rows, config=None):
        config = deepcopy(config or self.config)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'routes.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            config['routes']['file'] = file_evidence(path)
            return import_routes(config['routes'], config['requests'], self.layer)

    def test_full_model_arithmetic_matches_real_pool(self):
        service = DataService(self.config, self.layer)
        audit = service.evidence['model']
        self.assertEqual(len(audit['layer_structure']), 32)
        self.assertEqual(audit['executed_layer_ids'], [7])
        self.assertEqual(audit['weight_bytes_per_rank'], 25759850496)
        self.assertEqual(audit['kv_bytes_per_token_per_rank'], 131072)
        self.assertEqual(audit['kv_capacity_blocks'], 28164)
        self.assertEqual(audit['kv_block_bytes'], 2097152)
        for scheduler in service.schedulers:
            self.assertEqual(scheduler.memory.weight, audit['weight_bytes_per_rank'])
            self.assertEqual(scheduler.memory.npu_used, 26834640896)

    def test_normalized_zero_and_nonzero_segment_indices(self):
        records = self.routes(self.raw)
        self.assertEqual(sorted({r['token_index'] for r in records.values()}), [0,1,2])
        self.assertEqual(records['701',0,7]['source_index'],100)
        self.assertEqual(records['701',0,7]['source_token_index'],8)
        self.assertEqual(records['701',0,7]['source_line'],1)
        c = deepcopy(self.config)
        for r in c['requests']: r.update(generated_tokens=2, decode_steps=2)
        records = self.routes(self.raw[4:], c)
        self.assertEqual(sorted({r['token_index'] for r in records.values()}),[1,2])
        self.assertEqual(records['701',1,7]['source_index'],104)

    def test_bad_route_records_fail_without_fallback(self):
        changes = [lambda r:r.pop(), lambda r:r.append(deepcopy(r[0])),
            lambda r:r[0].update(phase='prefill'), lambda r:r[0].update(source_token_index=7),
            lambda r:r[0].update(layer_id=8), lambda r:r[0].update(expert_ids=[2,2]),
            lambda r:r[0].update(expert_ids=[2,8]), lambda r:r[0].update(request_id='unknown'),
            lambda r:r[0].update(source_index=101), lambda r:r[0].update(source_index=True)]
        for change in changes:
            with self.subTest(change=change):
                rows=deepcopy(self.raw); change(rows)
                with self.assertRaises(ValueError): self.routes(rows)

    def test_model_memory_and_digest_rejections(self):
        changes = [lambda c:c['model_file'].update(sha256='0'*64),
            lambda c:c['routes']['file'].update(sha256='0'*64),
            lambda c:c['memory'].update(device_bytes=20*2**30),
            lambda c:c['memory'].update(communication_buffer_bytes=8192),
            lambda c:c['memory'].update(block_size=True),
            lambda c:c['memory'].update(device_bytes=25759850496+2**30+2**20+2**21),
            lambda c:c['routes'].update(model='another-model')]
        for change in changes:
            with self.subTest(change=change):
                c=deepcopy(self.config); change(c)
                with self.assertRaises(ValueError): load_inputs(c,self.layer)
        for field,value in [('hidden_dim',256),('top_k',1),('layer_id',32),('dispatch_element_bytes',1)]:
            layer=deepcopy(self.layer);layer[field]=value
            with self.assertRaises(ValueError): load_inputs(self.config,layer)

    def test_exact_dense_profile_partial_reference(self):
        profile,evidence=import_compute(self.config['compute'],'float16',self.layer)
        self.assertEqual(profile['attention_route_pack'],87911)
        self.assertEqual([r['source_line'] for r in evidence['rows']],[3,4,5,6,8,9])
        self.assertEqual(sum(r['duration_ns'] for r in evidence['rows']),82911)
        self.assertEqual(profile['expert_per_input'],self.layer['compute_profile_ns']['expert_per_input'])
        self.assertIn('attention',evidence['unprofiled'])
        self.assertEqual(evidence['extrapolation_count'],0)
        with self.assertRaisesRegex(ValueError,'float16'):
            import_compute(self.config['compute'],'bfloat16',self.layer)
        # Test parser failures without writing to the locked dependency file.
        raw=PROFILE_PATH.read_bytes()
        for content,error in [(b'bad,header\n','CSV'),
                (raw.replace(b'q_proj,1,0,1,16086\n',b''),'Missing exact'),
                (raw+b'q_proj,1,0,1,16086\n','Duplicate profile')]:
            with self.subTest(error=error), patch('moe_ub_cosim.data_inputs.read_verified',return_value=(PROFILE_PATH.resolve(),content)):
                with self.assertRaisesRegex(ValueError,error):
                    import_compute(self.config['compute'],'float16',self.layer)

    def test_scheduler_consumes_only_normalized_selected_layer_records(self):
        service=DataService(self.config,self.layer)
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);(path/'graphs').mkdir()
            for step,now in enumerate([0,300000,600000,900000]):
                response=service.decide(dict(schema_version='cosim/1',type='DECIDE',run_id=self.config['run_id'],
                    decision_id=step,now_ns=now,completed=[] if not step else [dict(kind='WAVE_DONE',
                    graph_id=f'g{step-1}',wave_id=f'D.w{step-1}',finished_ns=now)],available_rank_ids=[0,1,2,3]),path)
                self.assertEqual(response['service_finished'],step==3)
                if step<3:
                    plan=json.loads((path/f'graphs/g{step}/plan.json').read_text())
                    self.assertEqual([t['token_index'] for t in plan['input']['tokens']],[step]*4)
                    self.assertEqual(len(plan['input_provenance']['route_records']),4)
            self.assertEqual(len(service.consumed),12)
            self.assertTrue(all(r['completed_segment_steps']==3 for r in service.snapshot()))
            self.assertTrue(all(row['kv_used_bytes']==0 for row in service.result_fields()['memory_final']))


if __name__=='__main__': unittest.main()
