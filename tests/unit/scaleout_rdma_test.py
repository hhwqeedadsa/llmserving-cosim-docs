"""Reject silent route/budget mistakes and check work identities across waves."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_rdma import validate_probe, validate_protocol
from moe_ub_cosim.scaleout_service import make_wave, routes, validate
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'examples/scaleout_rdma'


class RdmaTest(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((EXAMPLES/'service.json').read_text())
        self.r=json.loads((EXAMPLES/'rdma.json').read_text())
        self.tc=json.loads((EXAMPLES/'small-clos-100g.json').read_text())
        self.t,_=generate(self.tc)

    def test_short_message_ack_and_boolean_fields_rejected(self):
        bad=deepcopy(self.r);bad['cc_mode']=True
        with self.assertRaises(ValueError):validate_protocol(bad)
        bad=deepcopy(self.r);bad['ack_interval_bytes']=1000
        with self.assertRaisesRegex(ValueError,'short messages'):validate_protocol(bad)
        with self.assertRaises(ValueError):
            validate_probe(self.t,self.r,[dict(id=0,src=False,dst=4,bytes=999,plane=0,ready_ns=0)])

    def test_endpoint_cannot_bridge_planes(self):
        with self.assertRaisesRegex(ValueError,'packet-switch path'):
            validate_probe(self.t,self.r,[dict(id=0,src=0,dst=4,bytes=999,plane=1,ready_ns=0)])

    def test_fault_rules_validate_real_offsets_and_receive_ports(self):
        batch=[dict(id=50000,src=0,dst=4,bytes=4000,plane=0,ready_ns=0)]
        rule=dict(node=4,port=0,message_id=50000,kind='data',sequence=1000,start_ns=0,end_ns=5000000,count=2)
        self.r['faults']=[rule]
        validate_probe(self.t,self.r,batch)
        rule['sequence']=1001
        with self.assertRaisesRegex(ValueError,'actual data offset'):validate_probe(self.t,self.r,batch)
        rule.update(kind='ack',node=0,sequence=4000)
        validate_probe(self.t,self.r,batch)
        rule['port']=1024
        with self.assertRaisesRegex(ValueError,'node/port'):validate_probe(self.t,self.r,batch)

    def test_capture_and_fault_budgets_reject_ambiguous_inputs(self):
        for value in (0,1,'true',None):
            with self.assertRaisesRegex(ValueError,'boolean'):validate_protocol(dict(self.r,queue_metrics=value))
        validate_protocol(dict(self.r,queue_metrics=True))
        self.r['packet_capture']=dict(nodes=[0,4],start_ns=0,end_ns=5000000,max_events=100)
        validate_protocol(self.r)
        self.r['packet_capture']['max_events']=True
        with self.assertRaises(ValueError):validate_protocol(self.r)
        self.r.pop('packet_capture')
        self.r['faults']=[]
        with self.assertRaises(ValueError):validate_protocol(self.r)
        self.r['drop_once']=dict(message_id=0,dst_gpu=4,sequence=0)
        with self.assertRaisesRegex(ValueError,'not both'):validate_protocol(self.r)
        self.r.pop('faults')
        self.r['drop_once']['dst_gpu']=8
        with self.assertRaisesRegex(ValueError,'not a GPU'):
            validate_probe(self.t,self.r,[dict(id=0,src=0,dst=4,bytes=4000,plane=0,ready_ns=0)])

    def test_ids_disjoint_across_groups_layers_steps(self):
        self.c['parallel']['groups']=[[0,2,4,6],[1,3,5,7]]
        ids=set()
        for group_index,group in enumerate(self.c['parallel']['groups']):
            for step in range(4):
                wave=make_wave(self.c,group,group_index,step,2)
                fresh={m['id'] for m in wave['messages']}
                self.assertFalse(ids & fresh);ids.update(fresh)
                self.assertLess(max(fresh),50000)
                for m in wave['messages']:
                    self.assertIn(m['src'],group);self.assertIn(m['dst'],group)
        self.assertTrue(ids)

    def test_exact_half_hot_tokens_and_no_duplicate_experts(self):
        self.c['workload']['hotspot_fraction']=0.5
        selected=routes(self.c,list(range(8)),0,0)
        self.assertEqual(sum(0 in t['experts'] for t in selected),len(selected)//2)
        self.assertTrue(all(len(set(t['experts']))==2 for t in selected))

    def test_decode_context_is_not_network_token_count(self):
        first=make_wave(self.c,list(range(8)),0,0,1)
        self.c['workload']['context_tokens']*=2
        second=make_wave(self.c,list(range(8)),0,0,1)
        self.assertEqual(first['messages'],second['messages'])
        self.c['parallel']['groups']=[[0,1,2,3]]
        with self.assertRaisesRegex(ValueError,'partition'):validate(self.c,self.t,self.r)


if __name__=='__main__':unittest.main()
