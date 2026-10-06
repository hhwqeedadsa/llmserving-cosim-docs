"""Preparation accounting, directed rate validation and observation coverage."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan,validate_plan
from moe_ub_cosim.port_service import validate
from moe_ub_cosim.observation import execution_coverage
from moe_ub_cosim.route_admission import CannRouteAdmission

ROOT=Path(__file__).resolve().parents[2]


class EndpointServiceTest(unittest.TestCase):
    def layer(self):
        c=json.loads((ROOT/'fixtures/m11_native/layer.json').read_text())
        c['endpoint_prepare']=dict(schema_version='moe-ub-endpoint-prepare/1',relation='additional_to_existing_once',
            ranks=[dict(rank_id=r,dispatch_ns_per_local_token=101+i,combine_ns_per_expert_input=307+i)
                   for i,r in enumerate(c['rank_ids'])])
        return c

    def test_once_per_actual_input_and_original_work_preserved(self):
        c=self.layer();original=deepcopy(c);original.pop('endpoint_prepare')
        p=build_plan(c);base=build_plan(original)
        self.assertEqual(p['transfers'],base['transfers'])
        self.assertEqual(CannRouteAdmission(c).check(c['tokens'][0]),CannRouteAdmission(original).check(c['tokens'][0]))
        expected={2:{'attention-route-quant-pack':101,'expert-result-pack':307},
                  7:{},12:{'expert-result-pack':309}}
        for g,b in zip(p['rank_graphs'],base['rank_graphs']):
            self.assertEqual(len(g['nodes']),len(b['nodes']))
            for node,old in zip(g['nodes'],b['nodes']):
                want=deepcopy(old)
                if node['name'] in expected[g['rank_id']]:want['duration_ns']+=expected[g['rank_id']][node['name']]
                self.assertEqual(node,want)
        bad=deepcopy(p)
        next(n for n in bad['rank_graphs'][0]['nodes'] if n['name']=='attention-route-quant-pack')['duration_ns']+=101
        with self.assertRaises(ValueError):validate_plan(bad)

    def test_prepare_rejections_and_future_route_overflow(self):
        for v in (-1,True,1.5,2**63):
            c=self.layer();c['endpoint_prepare']['ranks'][0]['dispatch_ns_per_local_token']=v
            with self.assertRaises(ValueError):build_plan(c)
        c=self.layer();c['endpoint_prepare']['ranks'].reverse()
        with self.assertRaises(ValueError):build_plan(c)
        c=self.layer();c['endpoint_prepare']['relation']='replace'
        with self.assertRaises(ValueError):build_plan(c)
        c=self.layer();c['endpoint_prepare']['ranks'][0]['dispatch_ns_per_local_token']=2**63-1
        with self.assertRaises(ValueError):CannRouteAdmission(c).check(c['tokens'][0])

    def test_directed_intervals_and_rejections(self):
        topology=dict(links=[dict(src=0,src_port=3,dst=8,dst_port=1)])
        value=dict(schema_version='moe-ub-port-service/1',execution_end_ns=100,
                   intervals=[dict(node_id=0,port_id=3,start_ns=10,end_ns=20,rate_bps=8000000),
                              dict(node_id=0,port_id=3,start_ns=20,end_ns=50,rate_bps=16000000),
                              dict(node_id=8,port_id=1,start_ns=0,end_ns=100,rate_bps=4000000)])
        self.assertEqual(validate(value,topology,100),value)
        for key,v in [('rate_bps',0),('rate_bps',True),('start_ns',-1),('end_ns',101),('end_ns',10),('node_id',9),('port_id',0)]:
            bad=deepcopy(value);bad['intervals'][0][key]=v
            with self.assertRaises(ValueError):validate(bad,topology)
        bad=deepcopy(value);bad['intervals'][1]['start_ns']=19
        with self.assertRaises(ValueError):validate(bad,topology)
        with self.assertRaises(ValueError):validate(value,topology,99)

    def test_clock_coverage_is_separate_from_execution(self):
        config=json.loads((ROOT/'examples/first_round/observation.json').read_text())
        config['clocks'][-1].update(offset_ns=-3,drift_ppb=100000000)
        config['switches'][0].update(start_local_ns=-3,end_local_ns=1097)
        self.assertTrue(execution_coverage(config,[2,7,12],2000,999)['observation_complete'])
        result=execution_coverage(config,[2,7,12],2000,1000)
        self.assertTrue(result['execution_complete']);self.assertFalse(result['observation_complete'])
        self.assertEqual(len(result['incomplete_ports']),2)


if __name__=='__main__':unittest.main()
