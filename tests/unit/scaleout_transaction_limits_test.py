"""Admission boundaries independent of any network execution."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import execution_limits, validate

ROOT=Path(__file__).resolve().parents[2]


class TransactionLimitsTests(unittest.TestCase):
    def setUp(self):
        self.work=json.loads((ROOT/'examples/scaleout_rdma_transactions/execution-limits.json').read_text())
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_expanded_and_default_preserved(self):
        validate(self.topology,self.protocol,self.work)
        for schema in range(1,6):
            self.assertEqual(execution_limits(dict(schema_version=f'moe-rdma-transactions/{schema}')),
                dict(max_connections=8,max_remote_operations=32,max_local_operations=16))
        bad=deepcopy(self.work);del bad['execution_limits']
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)

    def test_declaration_types_bounds_and_schema(self):
        for key,lo,hi in [('max_connections',1,64),('max_remote_operations',1,1024),('max_local_operations',0,256)]:
            for value in [True,False,lo-1,hi+1,1.0,'1',None]:
                with self.subTest(key=key,value=value):
                    bad=deepcopy(self.work);bad['execution_limits'][key]=value
                    with self.assertRaises(ValueError):execution_limits(bad)
            for value in (lo,hi):
                good=deepcopy(self.work);good['execution_limits'][key]=value
                self.assertEqual(execution_limits(good)[key],value)
            bad=deepcopy(self.work);del bad['execution_limits'][key]
            with self.assertRaises(ValueError):execution_limits(bad)
        for value in (None,[],{},dict(self.work['execution_limits'],unexpected=0)):
            bad=deepcopy(self.work);bad['execution_limits']=value
            with self.assertRaises(ValueError):execution_limits(bad)
        for schema in (1,2,3,4,6):
            bad=deepcopy(self.work);bad['schema_version']=f'moe-rdma-transactions/{schema}'
            with self.assertRaises(ValueError):execution_limits(bad)

    def test_each_lifetime_limit_and_sparse_connection_id(self):
        for key,value in [('max_connections',11),('max_remote_operations',95),('max_local_operations',19)]:
            bad=deepcopy(self.work);bad['execution_limits'][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)
        bad=deepcopy(self.work);bad['connections'][-1]['id']=12
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)
        bad=deepcopy(self.work);bad['operations'][0]['connection']=12
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)

    def test_capacity_does_not_relax_safety(self):
        for mutation in ('owner','range','fence','depth','overwrite','rank'):
            bad=deepcopy(self.work)
            if mutation=='owner':bad['operations'][0]['source']['key']=8
            if mutation=='range':bad['operations'][0]['address']=4096+65536
            if mutation=='fence':bad['operations'][0]['queue_fence']=[11]
            if mutation=='depth':bad['connections'][0]['queue_depth']=64
            if mutation=='overwrite':bad['operations'][8]['address']=bad['operations'][0]['address'];bad['connections'][1]['dst']=1
            if mutation=='rank':bad['connections'][0]['src']=32
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)

    def test_fence_can_cover_more_than_eight_source_owned_queues(self):
        good=deepcopy(self.work)
        for c in good['connections']:c.update(src=0,dst=1)
        good['operations']=good['operations'][:1]
        good['operations'][0]['queue_fence']=list(range(12))
        validate(self.topology,self.protocol,good)
        good['connections'][11]['src']=4
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,good)


if __name__=='__main__':unittest.main()
