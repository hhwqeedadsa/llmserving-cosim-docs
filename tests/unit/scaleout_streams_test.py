"""Input boundary checks for an explicitly limited native transport probe."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_streams import validate

ROOT=Path(__file__).resolve().parents[2]


class StreamInputTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'examples/scaleout_rdma_streams/work.json').read_text())
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        # Physical graph used only to check input validation, never as native
        # execution evidence. Public workflow creates its own actual topology.
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000) for i in range(8)])

    def test_legal_causal_input(self):
        validate(self.topology,self.protocol,self.config)
        config=deepcopy(self.config);config['streams'][0]['queue_depth']=4096
        validate(self.topology,self.protocol,config)

    def test_reject_future_dependency_unknown_event_and_guessed_time(self):
        for after in ([dict(work=5,event='accepted')],[dict(work=0,event='visible')],
                      [dict(work=0,event='acked'),dict(work=0,event='acked')]):
            config=deepcopy(self.config);config['work'][1]['after']=after
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        config=deepcopy(self.config);config['work'][0]['ready_ns']=0
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)

    def test_reject_identity_depth_and_work_budget(self):
        for key,value in [('wire_port',21001),('queue_depth',3),('queue_depth',True),('src',32)]:
            config=deepcopy(self.config);config['streams'][0][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        for key,value in [('id',0),('stream',9),('bytes',0),('bytes',1048577)]:
            config=deepcopy(self.config);config['work'][1][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        config=deepcopy(self.config);config['work']*=11
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)

    def test_reject_unimplemented_transaction_and_legacy_modes(self):
        config=deepcopy(self.config);config['work'][0]['verb']='atomic'
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        for key in ('recovery','faults','admission','lifecycle_capture'):
            protocol=deepcopy(self.protocol);protocol[key]={}
            with self.assertRaises(ValueError):validate(self.topology,protocol,self.config)
        topology=deepcopy(self.topology);topology['intra_host']=dict(enabled=True)
        with self.assertRaises(ValueError):validate(topology,self.protocol,self.config)


if __name__=='__main__':unittest.main()
