"""Reject incomplete or contradictory drop evidence, including empty captures."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_switch_drops import analyze, COLUMNS, REASONS
from moe_ub_cosim.scaleout_rdma import validate_protocol, validate_probe
from moe_ub_cosim.scaleout_topology import generate

ROOT = Path(__file__).resolve().parents[2]


class SwitchDropTest(unittest.TestCase):
    def setUp(self):
        ex = ROOT/'examples/scaleout_rdma'
        self.p = json.loads((ex/'rdma.json').read_text())
        self.t, _ = generate(json.loads((ex/'small-clos-100g.json').read_text()))
        self.p['switch_drop_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000, max_events=2)
        self.r = dict(finish_ns=4000, switch_drop_capture=dict(schema_version='native-switch-drop-capture/1',
            selection=deepcopy(self.p['switch_drop_capture']), columns=COLUMNS, reason_codes=REASONS,
            all_switch_reason_counts=[1,0,0,0], events=[[100,8,1,2,3,1,10,0,17,0,1000,900]],
            observations=1, omitted_events=0, complete=True))

    def test_full_window_and_empty_observation(self):
        self.assertEqual(analyze(self.r,self.t,self.p,require_complete=True)['all_switch_drops'],1)
        d=self.r['switch_drop_capture'];d.update(all_switch_reason_counts=[0]*4,events=[],observations=0)
        self.assertEqual(analyze(self.r,self.t,self.p)['all_switch_drops'],0)

    def test_bad_options_rejected(self):
        for key,value in [('nodes',[0]),('nodes',[8,8]),('nodes',[True]),('start_ns',True),
                          ('end_ns',0),('max_events',100001),('max_events',True)]:
            p=deepcopy(self.p);p['switch_drop_capture'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                validate_probe(self.t,p,[dict(id=0,src=0,dst=4,bytes=1000,plane=0,ready_ns=0)])
        p=deepcopy(self.p);p['switch_drop_capture']['future']=1
        with self.assertRaises(ValueError):validate_protocol(p)

    def test_missing_truncated_and_false_complete_rejected(self):
        with self.assertRaises(ValueError):analyze(dict(finish_ns=4000),self.t,self.p)
        d=self.r['switch_drop_capture'];d.update(all_switch_reason_counts=[2,0,0,0],observations=2,omitted_events=1,complete=False)
        self.assertFalse(analyze(self.r,self.t,self.p)['complete'])
        with self.assertRaisesRegex(ValueError,'truncated'):analyze(self.r,self.t,self.p,require_complete=True)
        d['complete']=True
        with self.assertRaises(ValueError):analyze(self.r,self.t,self.p)

    def test_reason_time_port_and_counter_tampering(self):
        for index,value in [(0,5000000),(1,0),(2,99),(3,-1),(4,0),(5,99),(6,True),(11,1001)]:
            r=deepcopy(self.r);r['switch_drop_capture']['events'][0][index]=value
            with self.subTest(index=index),self.assertRaises(ValueError):analyze(r,self.t,self.p)
        r=deepcopy(self.r);r['switch_drop_capture']['all_switch_reason_counts']=[1,1,0,0]
        with self.assertRaises(ValueError):analyze(r,self.t,self.p)

    def test_scope_not_inferred_from_empty_filtered_detail(self):
        self.p['switch_drop_capture'].update(nodes=[9],start_ns=1000)
        d=self.r['switch_drop_capture'];d.update(selection=deepcopy(self.p['switch_drop_capture']),events=[],observations=0)
        self.assertEqual(analyze(self.r,self.t,self.p)['all_switch_drops'],1)
        with self.assertRaises(ValueError):analyze(self.r,self.t,dict(self.p,switch_drop_capture=dict(self.p['switch_drop_capture'],nodes=[])))

    def test_no_route_and_undeclared_capture(self):
        d=self.r['switch_drop_capture'];d['events'][0][3]=-1;d['events'][0][5]=3
        d['all_switch_reason_counts']=[0,0,1,0]
        self.assertEqual(analyze(self.r,self.t,self.p)['saved_events'],1)
        p=deepcopy(self.p);del p['switch_drop_capture']
        with self.assertRaises(ValueError):analyze(self.r,self.t,p)
        self.assertIsNone(analyze({},self.t,p))


if __name__ == '__main__':
    unittest.main()
