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

    def test_source_rate_capture_types_window_and_budget(self):
        config=deepcopy(self.config)
        valid=dict(start_ns=0,end_ns=5000000,max_events=1000)
        config['source_rate_capture']=valid
        validate(self.topology,self.protocol,config)
        for key,values in [('start_ns',[-1,True,5000000]),('end_ns',[0,5000001,True,1.5]),
                           ('max_events',[0,100001,True,'100'])]:
            for value in values:
                config['source_rate_capture']=dict(valid,**{key:value})
                with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        for value in ({},dict(valid,period_ns=1),dict(start_ns=0,max_events=1)):
            config['source_rate_capture']=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)

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

    def test_explicit_v2_counts_and_no_implicit_upgrade(self):
        config=deepcopy(self.config)
        config['schema_version']='moe-rdma-stream-work/2'
        config['limits']=dict(max_streams=2,max_work=128)
        config['work']=[dict(id=i,stream=0,bytes=1,after=[]) for i in range(128)]
        validate(self.topology,self.protocol,config)
        config['limits']['max_work']=127
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        config['schema_version']='moe-rdma-stream-work/1';del config['limits']
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)

    def test_reject_v2_budget_types_bounds_and_missing_fields(self):
        config=deepcopy(self.config);config['schema_version']='moe-rdma-stream-work/2'
        for value in ({},dict(max_streams=1,max_work=64),dict(max_streams=129,max_work=64),
                      dict(max_streams=16,max_work=8193),dict(max_streams=16,max_work=True),
                      dict(max_streams=16,max_work=64,extra=1)):
            config['limits']=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)
        config['schema_version']='moe-rdma-stream-work/1';config['limits']=dict(max_streams=16,max_work=64)
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,config)

    def test_v2_endpoint_budget_is_separate_from_stream_counts(self):
        from moe_ub_cosim.scaleout_limits import DEFAULTS
        protocol=deepcopy(self.protocol);protocol['schema_version']='moe-scaleout-rdma/2'
        protocol['limits']=dict(DEFAULTS,max_gpus=40)
        topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(40)]+[dict(id=40,kind='tor',plane=0)],
            links=[dict(src=i,dst=40,src_port=1,dst_port=i+1,rate_bps=100000000000) for i in range(40)])
        config=deepcopy(self.config);config['streams'][0]['src']=32
        with self.assertRaises(ValueError):validate(topology,protocol,config)
        config['schema_version']='moe-rdma-stream-work/2';config['limits']=dict(max_streams=2,max_work=6)
        validate(topology,protocol,config)
        protocol['limits']['max_gpus']=32
        with self.assertRaises(ValueError):validate(topology,protocol,config)


if __name__=='__main__':unittest.main()
