"""Validate complete bounded buffer candidates before any online side effect."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


class BoundedBuffersTests(unittest.TestCase):
    def setUp(self):
        self.program=json.loads((ROOT/'examples/scaleout_rdma_transactions/control-alarm.json').read_text())
        self.program['buffer_model']=dict(mode='bounded_copied_put/1',transport_bytes_per_source=40,
            registered_bytes_per_owner=256,target_cache_bytes_per_owner=40,local_copy_bytes_per_owner=8)
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_legal_boundaries_and_legacy(self):
        validate(self.topology,self.protocol,self.program)
        legacy=deepcopy(self.program);del legacy['buffer_model']
        validate(self.topology,self.protocol,legacy)

    def test_strict_capacity_types_and_ranges(self):
        for key in ('transport_bytes_per_source','registered_bytes_per_owner',
                    'target_cache_bytes_per_owner','local_copy_bytes_per_owner'):
            for value in (True,0,-1,1.5,'40',2**64):
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    p=deepcopy(self.program);p['buffer_model'][key]=value
                    validate(self.topology,self.protocol,p)

    def test_model_fields_and_supported_mode(self):
        for model in (True,{},dict(self.program['buffer_model'],unknown=1),
                      dict(self.program['buffer_model'],mode='zero_copy')):
            p=deepcopy(self.program);p['buffer_model']=model
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)

    def test_frame_header_is_charged(self):
        p=deepcopy(self.program);p['buffer_model']['transport_bytes_per_source']=39
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)

    def test_fixed_windows_and_local_snapshot_have_separate_caps(self):
        for key,value in [('registered_bytes_per_owner',127),('local_copy_bytes_per_owner',7)]:
            p=deepcopy(self.program);p['buffer_model'][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)

    def test_target_cache_cumulative_admission_is_pure(self):
        p=deepcopy(self.program)
        op=deepcopy(p['operations'][0]);op.update(id=2,address=8240,after=[])
        p['operations'].append(op);before=deepcopy(p)
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)
        self.assertEqual(p,before)
        p['buffer_model']['target_cache_bytes_per_owner']=80
        validate(self.topology,self.protocol,p)

    def test_inline_source_is_not_silently_copied(self):
        p=deepcopy(self.program);op=p['operations'][0];op.pop('source');op['hex']='0102030405060708'
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)

    def test_isolated_copy_release_is_explicit_and_preserves_source_contract(self):
        p=deepcopy(self.program)
        p['buffer_model']['on_source_isolation']='release_copied_payload/1'
        p['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=20000,
            max_rto_ns=20000,max_retries=2,max_events=100,on_exhausted='isolate_stream')
        before=deepcopy(p)
        validate(self.topology,self.protocol,p)
        self.assertEqual(p,before)
        self.assertEqual(p['source_model']['release'],'transaction_completed/1')

    def test_isolated_copy_release_cannot_use_stop_or_absent_recovery(self):
        p=deepcopy(self.program);p['buffer_model']['on_source_isolation']='release_copied_payload/1'
        with self.assertRaisesRegex(ValueError,'explicit source isolation'):
            validate(self.topology,self.protocol,p)
        for policy in (None,'stop_simulation'):
            p['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=20000,
                max_rto_ns=20000,max_retries=2,max_events=100)
            if policy:p['transport_recovery']['on_exhausted']=policy
            with self.assertRaisesRegex(ValueError,'explicit source isolation'):
                validate(self.topology,self.protocol,p)

    def test_isolated_copy_release_rejects_ambiguous_values(self):
        for value in (True,False,0,1,None,{},'release_all',''):
            p=deepcopy(self.program);p['buffer_model']['on_source_isolation']=value
            p['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=20000,
                max_rto_ns=20000,max_retries=2,max_events=100,on_exhausted='isolate_stream')
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate(self.topology,self.protocol,p)

    def test_carrier_once_put_avoids_cumulative_cache_admission(self):
        p=deepcopy(self.program)
        op=deepcopy(p['operations'][0]);op.update(id=2,address=8240,after=[])
        p['operations'].append(op)
        p['buffer_model'].update(target_delivery='carrier_once_put/1',target_cache_bytes_per_owner=0)
        before=deepcopy(p);validate(self.topology,self.protocol,p);self.assertEqual(p,before)
        for key,value in (('transport_bytes_per_source',39),('registered_bytes_per_owner',127),('local_copy_bytes_per_owner',7)):
            broken=deepcopy(p);broken['buffer_model'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(self.topology,self.protocol,broken)

    def test_carrier_once_put_requires_explicit_exact_mode_and_zero_cache(self):
        for mode,capacity in ((True,0),({},0),('once',0),('carrier_once_put/1',40),('carrier_once_put/1',True)):
            p=deepcopy(self.program);p['buffer_model'].update(target_delivery=mode,target_cache_bytes_per_owner=capacity)
            with self.subTest(mode=mode,capacity=capacity),self.assertRaises(ValueError):validate(self.topology,self.protocol,p)
        p=deepcopy(self.program);p['buffer_model']['target_cache_bytes_per_owner']=0
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,p)

    def test_carrier_once_put_combines_with_explicit_source_isolation(self):
        p=deepcopy(self.program)
        p['buffer_model'].update(target_delivery='carrier_once_put/1',target_cache_bytes_per_owner=0,
            on_source_isolation='release_copied_payload/1')
        p['transport_recovery']=dict(mode='go_back_n/1',initial_rto_ns=20000,
            max_rto_ns=20000,max_retries=2,max_events=100,on_exhausted='isolate_stream')
        validate(self.topology,self.protocol,p)


if __name__=='__main__':unittest.main()
