from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate, _local_waits
from moe_ub_cosim.transaction_session import _wake_plan

ROOT=Path(__file__).resolve().parents[2]


class Atomic32Inputs(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_bytes())
        self.config['operations']=[dict(id=0,connection=0,verb='fetch_add32',key=7,address=4100,value=2**32-1,after=[])]
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_bytes())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_width_permissions_and_boundary(self):
        validate(self.topology,self.protocol,self.config)
        for name,value in [('value',2**32),('value',-1),('value',True),('address',4098),('address',4160)]:
            c=deepcopy(self.config);c['operations'][0][name]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)
        c=deepcopy(self.config);c['windows'][0]['allow_atomic']=False
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)
        c=deepcopy(self.config);c['windows'][0]['address']=4100;c['windows'][0]['hex']='00000000'
        validate(self.topology,self.protocol,c)
        c['operations'][0]['verb']='fetch_add'
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_wait_width_and_disjoint_neighbors(self):
        window=dict(address=4096,allow_atomic=True)
        registered={(4,7):(window,16)}
        base=dict(key=7,address=4100,compare='eq',value=2**32-1,reset_to=0,bits=32)
        _local_waits([base,dict(base,address=4104)],4,registered)
        for key,value in [('bits',True),('bits',16),('bits',32.0),('value',2**32),('reset_to',2**32),('address',4102)]:
            with self.assertRaises(ValueError):_local_waits([dict(base,**{key:value})],4,registered)
        with self.assertRaises(ValueError):_local_waits([dict(base,address=4096,bits=64),base],4,registered)
        _local_waits([dict(base,address=4096,bits=64,value=2**64-1)],4,registered)

    def test_mixed_atomic_effects_remain_atomic(self):
        for width in ('fetch_add','fetch_add32'):
            c=deepcopy(self.config)
            c['operations'].append(dict(id=1,connection=1,verb=width,key=7,address=4096,value=1,after=[]))
            validate(self.topology,self.protocol,c)
            c['operations'][1].update(verb='put',hex='0000000000000000');del c['operations'][1]['value']
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_response_subscription_supports_both_atomic_widths(self):
        c=deepcopy(self.config);c['operations'][0]['kind']='remote'
        wake=[dict(operation=0,event='response')]
        self.assertIsNotNone(_wake_plan(c,wake,set()))
        c['operations'][0]['verb']='put'
        with self.assertRaises(ValueError):_wake_plan(c,wake,set())


if __name__=='__main__':unittest.main()
