"""Resource preflight must reject before building message/node dictionaries."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.full_model import build_full_model
from moe_ub_cosim.resources import cann_usage, check_graph, check_run, validate_budget
from moe_ub_cosim.plan import RunIdentities, build_plan
from moe_ub_cosim.observation import validate_observation, backend_observation, validate_observed, public_config, write_csv, PORT_FIELDS

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads((ROOT/p).read_text())


class ResourceBudgetTest(unittest.TestCase):
    def test_independent_counts_and_preflight_rejection(self):
        c=read('fixtures/m10_cann/layers.json')['groups'][0]['layers'][0]
        # 12 status stores; one local-rank input and one cross-chip expert input.
        # At H=1024/chunk=1024, vector needs 2 dispatch + 2 combine + 1 status.
        self.assertEqual(cann_usage(c),dict(transfers=17,fabric_transfers=13,local_transfers=4,nodes=43))
        p=build_plan(c);self.assertEqual(len(p['transfers']),17)
        c['chunk_bytes']=1;c['resource_budget']=read('fixtures/m10_resources/graph-budget.json')
        c['resource_budget']['max_graph_transfers']=100
        with patch('moe_ub_cosim.cann.split',side_effect=AssertionError('constructed messages')):
            with self.assertRaisesRegex(ValueError,'resource budget'):build_plan(c)

    def test_full_model_budget_before_child_construction(self):
        c=read('fixtures/m10_full_model/service.json');l=read('fixtures/m10_full_model/layers.json')['groups'][0]['layers']
        b=read('fixtures/m10_resources/graph-budget.json')
        b['max_graph_transfers']=5000
        for row in l:row['resource_budget']=deepcopy(b);row['chunk_bytes']=1
        with patch('moe_ub_cosim.full_model.build_children',side_effect=AssertionError('constructed children')):
            with self.assertRaisesRegex(ValueError,'resource budget'):
                build_full_model(l,RunIdentities(),[[]]*3,c['groups'][0]['model_spec'],c['groups'][0]['full_model_profile'])

    def test_explicit_budget_is_not_default_limit_change(self):
        c=read('fixtures/m10_cann/layers.json')['groups'][0]['layers'][0]
        c['chunk_bytes']=1;usage=cann_usage(c)
        self.assertGreater(usage['transfers'],3000)
        for invalid in (0,True,-1,1.5):
            b=read('fixtures/m10_resources/graph-budget.json');b['max_graph_nodes']=invalid
            with self.assertRaises(ValueError):validate_budget(b)
        b=read('fixtures/m10_resources/graph-budget.json');b['max_run_graphs']=1
        used=check_run(dict(graphs=0,transfers=0,nodes=0),usage,b)
        with self.assertRaisesRegex(ValueError,'Run resource'):check_run(used,usage,b)
        with self.assertRaisesRegex(ValueError,'4096'):check_graph(dict(transfers=4097,nodes=8200))

    def test_observation_extended_limits_and_streamed_public_check(self):
        b=read('fixtures/m10_resources/observation-budget.json')
        c=dict(clocks=[dict(device_type=t,device_id=i,offset_ns=0,drift_ppb=0) for t,i in [('rank',0),('switch',8)]],
            switches=[dict(switch_id=8,port_ids=list(range(17)),start_local_ns=0,end_local_ns=5000,period_ns=1)],record_provenance=True,resource_budget=b)
        _,ports=validate_observation(c,[0],5000)
        self.assertEqual(len(ports),17);self.assertEqual(backend_observation(c,[0],5000)['schema_version'],'moe-ub-port-observation-set/2')
        old=deepcopy(c);old.pop('resource_budget')
        with self.assertRaises(ValueError):validate_observation(old,[0],5000)
        bad=deepcopy(c);bad['resource_budget']['max_total_windows']=84000
        with self.assertRaisesRegex(ValueError,'total window'):validate_observation(bad,[0],5000)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'public_config.json').write_text(json.dumps(public_config([0],ports,b)));(p/'endpoint_ops.jsonl').write_text('')
            write_csv(p/'port_windows.csv',PORT_FIELDS,(dict(switch_id=8,port_id=i,direction='egress',local_window_start_ns=j,
                local_window_end_ns=j+1,counted_packets=0,counted_packet_bytes=0) for i in range(17) for j in range(5000)))
            result=validate_observed(p);self.assertEqual(result['zero_windows'],85000)
            with (p/'port_windows.csv').open('a') as stream:stream.write('8,16,egress,5000,5001,0,0\n')
            with self.assertRaisesRegex(ValueError,'Missing, duplicated'):validate_observed(p)


if __name__=='__main__':unittest.main()
