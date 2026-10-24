"""Boundary checks for a finite byte-only selective-repeat reference."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_streams import validate

ROOT=Path(__file__).resolve().parents[2]


class SelectiveInputTests(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'examples/scaleout_rdma_streams/work-reference.json').read_text())
        self.config['transport_recovery']=dict(mode='selective_repeat/1',initial_rto_ns=80000,
            max_rto_ns=640000,max_retries=3,max_events=10000,receive_buffer_bytes=8192,inflight_bytes=4000)
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_explicit_reference(self):
        validate(self.topology,self.protocol,self.config)

    def test_finite_capacity_and_packet_overshoot(self):
        for key,value in [('receive_buffer_bytes',True),('receive_buffer_bytes',4998),
                          ('receive_buffer_bytes',2**21+1),('inflight_bytes',True),
                          ('inflight_bytes',999),('inflight_bytes',8192),('max_events',0)]:
            c=deepcopy(self.config);c['transport_recovery'][key]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_required_fields_and_control_mode(self):
        for key in ('receive_buffer_bytes','inflight_bytes'):
            c=deepcopy(self.config);del c['transport_recovery'][key]
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)
        c=deepcopy(self.config);del c['dcqcn_reference']
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)
        c=deepcopy(self.config);c['transport_recovery']['hidden_truth']=True
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_classic_mode_does_not_accept_selective_fields(self):
        c=deepcopy(self.config);c['transport_recovery']['mode']='go_back_n/1'
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,c)

    def test_transactions_do_not_accept_byte_only_selective_mode(self):
        from moe_ub_cosim.scaleout_transactions import validate as validate_transaction
        c=json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_text())
        c['transport_recovery']=deepcopy(self.config['transport_recovery'])
        with self.assertRaises(ValueError):validate_transaction(self.topology,self.protocol,c)


if __name__=='__main__':unittest.main()
