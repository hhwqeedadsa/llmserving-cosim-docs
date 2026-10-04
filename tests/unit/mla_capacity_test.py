"""Independent MLA tensor arithmetic and runtime/capacity separation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.composed_service import ComposedService
from moe_ub_cosim.evidence_service import EvidenceService
from moe_ub_cosim.mla_capacity import audit_inventory

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT/'fixtures/m10_mla_capacity'


def read(name):
    return json.loads((FIXTURE/name).read_text())


def audit(config):
    return audit_inventory(config['model_spec'], config['memory'], config['rank_ids'])


def layers():
    return json.loads((ROOT/'fixtures/m10_cann/layers.json').read_text())


class MlaCapacityTest(unittest.TestCase):
    def test_hand_count_tensors_scales_and_nonuniform_placement(self):
        result = audit(read('toy-capacity.json'))
        self.assertEqual(result['execution_scope'], 'capacity_only')
        self.assertEqual(result['communication_budget_basis'], 'declared_not_graph_validated')
        self.assertEqual([r['weight_bytes'] for r in result['ranks']], [1748,1908])
        self.assertEqual([r['parameter_count'] for r in result['ranks']], [911,1007])
        self.assertEqual([r['tensor_bytes'] for r in result['ranks']], [1300,1396])
        self.assertEqual([r['scale_bytes'] for r in result['ranks']], [448,512])
        for row in result['ranks']:
            self.assertEqual((row['kv_bytes_per_token'],row['kv_block_bytes'],row['kv_capacity_blocks']), (16,64,4))
            self.assertEqual(row['shared_components'], dict(embedding=256,final_norm=16,lm_head=256))
            dense = row['layers'][0]
            self.assertEqual(dense['weight_bytes'],540)
            self.assertEqual(dense['components'],dict(mla_q_a=48,mla_q_b=64,mla_kv_a=48,mla_kv_b=48,
                mla_o=64,block_norms=32,q_norm=8,kv_norm=4,dense_ffn_gate_up=144,dense_ffn_down=80))
            tensors = {t['name']:t for t in dense['tensors']}
            self.assertEqual(tensors['mla_kv_a']['shape'],[4,8])
            self.assertEqual(tensors['mla_kv_a']['scale_bytes'],16)
            self.assertEqual(tensors['mla_kv_b']['shape'],[8,2])
            self.assertEqual(tensors['mla_o']['shape'],[8,4])
            moe=row['layers'][1]
            self.assertEqual(moe['components']['shared_experts_gate_up'],48)
            self.assertEqual(moe['components']['shared_experts_down'],48)
            self.assertEqual(moe['components']['router'],96)
            self.assertEqual(moe['components']['router_correction'],12)
        self.assertEqual([r['layers'][1]['local_experts'] for r in result['ranks']],[1,2])

    def test_r1_independent_previous_audit_constants(self):
        result=audit(read('r1-capacity.json'))
        self.assertEqual(len(result['ranks']),128)
        for row in result['ranks']:
            self.assertEqual(row['parameter_count'],22226310656)
            self.assertEqual(row['tensor_bytes'],24400011264)
            self.assertEqual(row['scale_bytes'],24624384)
            self.assertEqual(row['weight_bytes'],24424635648)
            self.assertEqual(row['kv_bytes_per_token'],70272)
            self.assertEqual(row['kv_block_bytes'],1124352)
            self.assertEqual(row['kv_capacity_blocks'],27861)
            self.assertEqual(row['device_bytes']-row['non_kv_bytes'],31325587712)
            self.assertEqual([l['kind'] for l in row['layers']],['dense']*3+['moe']*58)
        self.assertFalse(result['full_model_execution_validated'])
        self.assertFalse(result['quantized_kernels_executed'])
        self.assertFalse(result['mla_kernel_validated'])

    def test_kv_dtype_and_tied_weight_storage(self):
        c=read('toy-capacity.json');c['model_spec']['dtypes']['kv']='float32'
        self.assertEqual([(r['kv_bytes_per_token'],r['kv_capacity_blocks']) for r in audit(c)['ranks']],[(32,2)]*2)
        c=read('toy-capacity.json');c['model_spec']['tie_word_embeddings']=True
        c['memory']['max_kv_blocks_per_rank']=8
        self.assertEqual([r['weight_bytes'] for r in audit(c)['ranks']],[1492,1652])
        self.assertEqual([r['kv_capacity_blocks'] for r in audit(c)['ranks']],[8,8])

    def test_authorized_target_communication_reserve(self):
        change=json.loads((ROOT/'fixtures/m10_resource_efficiency/communication-reserve.json').read_text())
        c=read('r1-capacity.json')
        self.assertEqual(change['minimum_bytes_per_layer']*change['layer_count'],change['effective_bytes_per_rank'])
        for rank in c['memory']['ranks']:
            self.assertEqual(rank['communication_buffer_bytes'],change['original_bytes_per_rank'])
            self.assertEqual(rank['device_bytes'],change['device_bytes_per_rank'])
            rank['communication_buffer_bytes']=change['effective_bytes_per_rank']
        for row in audit(c)['ranks']:
            self.assertEqual(row['weight_bytes'],24424635648)
            self.assertEqual(row['non_kv_bytes'],38936927488)
            self.assertEqual(row['kv_capacity_blocks'],22291)
            self.assertEqual(row['device_bytes']-row['non_kv_bytes'],25063072512)

    def test_unquantized_matrices_remove_scales(self):
        c=read('toy-capacity.json')
        for group in ('mla','dense_ffn','routed_experts','shared_experts'):
            c['model_spec']['weight_storage'][group].update(dtype='bfloat16',quantization='none',scale_dtype=None)
        # INT8 matrix elements: 576 / 672, previous scales: 448 / 512.
        for row,delta in zip(c['memory']['ranks'],[128,160]):row['device_bytes']+=delta
        rows=audit(c)['ranks']
        self.assertEqual([r['weight_bytes'] for r in rows],[1876,2068])
        self.assertEqual([r['scale_bytes'] for r in rows],[0,0])
        self.assertEqual([r['kv_capacity_blocks'] for r in rows],[4,4])

    def test_storage_not_kernel_and_unique_native_pool(self):
        service=ComposedService(read('service.json'),layers())
        for r in service.ranks:
            scheduler=service.scheduler_by_rank[r];row=service.audit_by_rank[r]
            self.assertEqual(row['weight_bytes'],10408520)
            self.assertEqual(row['kv_bytes_per_token'],384)
            self.assertEqual(row['kv_block_bytes'],1536)
            self.assertEqual(row['kv_capacity_blocks'],8)
            self.assertIs(scheduler.kv, scheduler.memory.kv)
            self.assertIs(scheduler.kv.npu_pool, scheduler.memory.npu_pool)
            self.assertEqual(scheduler.memory.get_kv(7),2688)
        self.assertEqual(service.evidence['groups']['ep0']['model']['executed_layer_ids'],[1,2,3])
        self.assertEqual(service.evidence['groups']['ep0']['model']['unexecuted_layer_ids'],[0])
        evidence=EvidenceService(read('evidence.json'),layers())
        self.assertEqual(evidence.requirements[201]['reserved_kv_blocks'],2)
        self.assertEqual(evidence.requirements[202]['reserved_kv_blocks'],2)
        self.assertEqual(evidence.requirements[203]['reserved_kv_blocks'],3)
        self.assertEqual(evidence.request_target(evidence.config['requests'][0]),float('inf'))

    def test_unsupported_storage_shape_and_capacity(self):
        original=read('toy-capacity.json')
        mutations=[
            lambda c:c['model_spec'].update(schema_version='moe-ub-model/1'),
            lambda c:c['model_spec'].update(tp=2),
            lambda c:c['model_spec']['attention'].update(q_lora_rank=0),
            lambda c:c['model_spec']['attention'].update(qk_rope_head_dim=3),
            lambda c:c['model_spec']['attention'].update(num_kv_heads=2),
            lambda c:c['model_spec']['attention'].update(num_heads=2**63-1),
            lambda c:c['model_spec']['kv_cache'].update(layout='expanded_kv'),
            lambda c:c['model_spec']['kv_cache'].update(quantization='int8'),
            lambda c:c['model_spec']['kv_cache'].update(padding_bytes_per_token=True),
            lambda c:c['model_spec']['dtypes'].update(weights='int8'),
            lambda c:c['model_spec']['dtypes'].update(kv='fp8'),
            lambda c:c['model_spec']['weight_storage']['mla'].update(dtype='int4'),
            lambda c:c['model_spec']['weight_storage']['mla'].update(scale_dtype='float16'),
            lambda c:c['model_spec']['weight_storage']['mla'].update(quantization='symmetric_per_input_channel'),
            lambda c:c['model_spec']['weight_storage']['mla'].update(padding_bytes=1),
            lambda c:c['model_spec']['weight_storage']['mla'].update(zero_point=1),
            lambda c:c['model_spec']['weight_storage']['norm'].update(scale_dtype='float32'),
            lambda c:c['model_spec']['weight_storage']['norm'].update(dtype='int8'),
            lambda c:c['model_spec']['layers'][1].update(shared_experts=0),
            lambda c:c['model_spec']['layers'][1]['expert_placement'].__setitem__(0,99),
            lambda c:c['rank_ids'].__setitem__(1,2),
            lambda c:c['memory']['ranks'][1].update(rank_id=2),
            lambda c:c['memory'].update(capacity_scope='whole_chip'),
            lambda c:c['memory'].update(max_kv_blocks_per_rank=3),
            lambda c:c['memory']['ranks'][0].update(device_bytes=1980),
        ]
        for index,mutate in enumerate(mutations):
            with self.subTest(index=index):
                c=deepcopy(original);mutate(c)
                with self.assertRaises(ValueError):audit(c)

    def test_execution_boundaries_and_early_impossible_request(self):
        c=read('service.json');l=layers()
        c['groups'][0]['model_spec']['layers'][1].update(shared_experts=1,shared_intermediate_size=256)
        for r in c['groups'][0]['memory']['ranks']:r['device_bytes']+=792576
        with self.assertRaisesRegex(ValueError,'shared-expert execution'):ComposedService(c,l)
        c=read('service.json');c['groups'][0]['model_spec']['layers'][1]=dict(layer_id=1,kind='dense',intermediate_size=256)
        for r in c['groups'][0]['memory']['ranks']:r['device_bytes']-=817176
        with self.assertRaisesRegex(ValueError,'must be MoE'):ComposedService(c,l)
        c=read('service.json');l=layers();l['groups'][0]['layers'][0]['operator_profile']={}
        with self.assertRaisesRegex(ValueError,'GQA operator profiles'):ComposedService(c,l)
        c=read('service.json');l=layers()
        for r in c['groups'][0]['memory']['ranks']:r['device_bytes']-=6*1536
        with self.assertRaisesRegex(ValueError,'final KV demand 3 blocks'):ComposedService(c,l)
        c=read('service.json')
        for r in c['groups'][0]['memory']['ranks']:r['communication_buffer_bytes']-=1;r['device_bytes']-=1
        with self.assertRaisesRegex(ValueError,'complete layer-segment bound'):ComposedService(c,l)


if __name__=='__main__':unittest.main()
