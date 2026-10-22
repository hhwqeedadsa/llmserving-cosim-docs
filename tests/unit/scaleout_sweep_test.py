"""Sweep configuration isolation, closed inputs and conservative result labels."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_sweep import merge,resolve

ROOT=Path(__file__).resolve().parents[2]


def spec():
    return dict(schema_version='moe-scaleout-sweep/1',base=dict(
        topology='examples/scaleout_rdma/small-clos-100g.json',
        service='examples/scaleout_rdma_resilience/service.json',
        protocol='examples/scaleout_rdma_resilience/combined-service.json'),
        budget=dict(wall_seconds=300,rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=268435456),
        cases=[dict(name='baseline',patches={},comparison_group='same'),
               dict(name='history-off',patches=dict(protocol=dict(recovery=dict(history=None))),comparison_group='same')])


class SweepTest(unittest.TestCase):
    def test_recursive_patch_copies_and_explicit_delete(self):
        original=dict(a=dict(x=1,y=[2,3]));changed=merge(original,dict(a=dict(x=None,y=[4])))
        self.assertEqual(changed,dict(a=dict(y=[4])));self.assertEqual(original['a']['x'],1)
        changed['a']['y'].append(5);self.assertEqual(original['a']['y'],[2,3])
        for patch in [dict(absent=None),dict(a=3,b=dict(c=None))]:
            with self.assertRaises(ValueError):merge(original,patch)

    def test_resolve_all_cases_and_source_isolation(self):
        c=spec();resolved=resolve(c,ROOT)
        self.assertIn('history',resolved['cases'][0]['protocol']['recovery'])
        self.assertNotIn('history',resolved['cases'][1]['protocol']['recovery'])
        self.assertEqual(c,spec())

    def test_invalid_or_misleading_cases_rejected_before_run(self):
        bad=[]
        c=spec();c['cases'][1]['name']='../escape';bad.append(c)
        c=spec();c['cases'][1]['name']='baseline';bad.append(c)
        c=spec();c['cases'][0]['expect']='any_failure';bad.append(c)
        c=spec();c['cases'][0]['patches']['command']={};bad.append(c)
        c=spec();c['cases'][0]['patches']=dict(service=dict(workload=dict(batch_per_rank=True)));bad.append(c)
        c=spec();c['cases'][1]['patches']['service']=dict(workload=dict(batch_per_rank=1));bad.append(c)
        c=spec();c['budget']['artifact_bytes']=True;bad.append(c)
        c=spec();c['cases']=[];bad.append(c)
        c=spec();c['cases']*=17;bad.append(c)
        c=spec();c['cases'][0]['patches']=dict(protocol=dict(recovery=dict(unknown=1)));bad.append(c)
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):resolve(value,ROOT)


if __name__=='__main__':unittest.main()
