"""Self-provider explicit admission; old paths and real ACK semantics remain."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


class SelfPeerTests(unittest.TestCase):
    def setUp(self):
        self.w=json.loads((ROOT/'examples/scaleout_self_peer/program.json').read_text())
        self.p=json.loads((ROOT/'examples/scaleout_context_transactions/rdma.json').read_text())
        self.t=dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_explicit_mixed_self_put_atomic_and_received_source(self):validate(self.t,self.p,self.w)

    def test_prefix_publication_keeps_self_and_remote_identities(self):
        self.w['submission_model']=dict(mode='explicit_prefix/1')
        for op in self.w['operations']:
            if op['kind']=='remote':op['aggregate_requests']=False
        validate(self.t,self.p,self.w)

    def test_opt_in_schema_and_unused_model(self):
        for modify in (lambda w:w.pop('self_peer_model'),lambda w:w.update(schema_version='moe-rdma-transactions/4'),
            lambda w:w['self_peer_model'].update(extra=True)):
            w=deepcopy(self.w);modify(w)
            with self.assertRaises(ValueError):validate(self.t,self.p,w)

    def test_self_has_no_wire_ports_or_nonself_owner(self):
        for key,value in [('request_port',24002),('response_port',24003),('dst',0),('destination_nic',2),('plane',1),('source_nic',3)]:
            w=deepcopy(self.w);w['connections'][1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate(self.t,self.p,w)

    def test_service_times_are_typed_ordered_and_bounded(self):
        for service in [dict(apply_ns=True,complete_ns=3),dict(apply_ns=4,complete_ns=3),dict(apply_ns=0,complete_ns=1000001),
                        dict(apply_ns=0,complete_ns=-1),dict(apply_ns=0,complete_ns=0,extra=1)]:
            w=deepcopy(self.w);w['connections'][1]['self_service']=service
            with self.subTest(service=service),self.assertRaises(ValueError):validate(self.t,self.p,w)
        self.w['connections'][1]['self_service']=dict(apply_ns=0,complete_ns=0);validate(self.t,self.p,self.w)

    def test_no_network_ack_can_be_waited_for_self(self):
        self.w['operations'][5]['after']=[dict(operation=3,event='request_acked')]
        with self.assertRaisesRegex(ValueError,'no network request ACK'):validate(self.t,self.p,self.w)

    def test_self_only_and_bad_depth_are_rejected(self):
        for depth in (0,3,33,False):
            w=deepcopy(self.w);w['connections'][1]['queue_depth']=depth
            with self.subTest(depth=depth),self.assertRaises(ValueError):validate(self.t,self.p,w)
        self.w['connections']=[c for c in self.w['connections'] if 'self_service' in c]
        with self.assertRaisesRegex(ValueError,'at least one remote'):validate(self.t,self.p,self.w)

    def test_permission_and_effect_order_guards_cannot_be_bypassed(self):
        w=deepcopy(self.w);w['windows'][1]['allow_atomic']=False
        with self.assertRaises(ValueError):validate(self.t,self.p,w)
        w=deepcopy(self.w);w['operations'][2]['after']=[]
        with self.assertRaises(ValueError):validate(self.t,self.p,w)


if __name__=='__main__':unittest.main()
