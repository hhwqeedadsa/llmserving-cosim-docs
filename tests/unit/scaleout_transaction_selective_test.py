from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


class TransactionSelectiveTests(unittest.TestCase):
    def setUp(self):
        self.work=json.loads((ROOT/'examples/scaleout_rdma_transactions/selective-chain.json').read_text())
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_valid_selective_and_same_reference_backoff(self):
        validate(self.topology,self.protocol,self.work)
        c=deepcopy(self.work);c['transport_recovery']['mode']='go_back_n/1'
        del c['transport_recovery']['inflight_bytes'];del c['transport_recovery']['receive_buffer_bytes']
        validate(self.topology,self.protocol,c)

    def test_explicit_reference_and_v5_required(self):
        for mode in ('missing','v4','pool'):
            c=deepcopy(self.work)
            if mode=='missing':del c['dcqcn_reference']
            if mode=='v4':c['schema_version']='moe-rdma-transactions/4'
            if mode=='pool':c['buffer_model']={}
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_reference_validation_is_not_bypassed(self):
        for key,value in [('mode','unknown'),('gain',True),('gain',0),('gain',2),('max_events',True),
                          ('cnp_model_bytes',29),('alpha_interval_ns',50000),('np_interval_ns',5000001)]:
            c=deepcopy(self.work);c['dcqcn_reference'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):validate(self.topology,self.protocol,c)
        c=deepcopy(self.work);c['dcqcn_reference']['extra']=0
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_finite_reorder_window_and_memory_permissions(self):
        for mode in ('window','capacity','permission','range'):
            c=deepcopy(self.work)
            if mode=='window':c['transport_recovery']['inflight_bytes']=True
            if mode=='capacity':c['transport_recovery']['receive_buffer_bytes']=4000
            if mode=='permission':c['windows'][1]['allow_atomic']=False
            if mode=='range':c['operations'][0]['source']['address']=999999999
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate(self.topology,self.protocol,c)


if __name__=='__main__':unittest.main()
