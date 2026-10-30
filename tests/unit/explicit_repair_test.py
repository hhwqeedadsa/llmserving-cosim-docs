"""Public repair configuration and bounded native header/path checks."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_link_failures import validate_options, validate_topology

ROOT=Path(__file__).resolve().parents[2]


class ExplicitRepairTest(unittest.TestCase):
    def setUp(self):
        self.topology=generate(json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes()))[0]
        self.protocol=json.loads((ROOT/'examples/scaleout_clos128/rdma.json').read_bytes())
        self.protocol['local_failover']=dict(mode='explicit_prefix/1', max_events=10000)
        self.protocol['link_failures']=dict(mode='switch_links/1',events=[dict(src=128,dst=132,time_ns=4000,route_update_delay_ns=60000)])

    def test_public_mode_and_temporary_restore(self):
        validate_topology(self.topology,self.protocol)
        self.protocol['link_failures']['mode']='switch_links/2'
        self.protocol['link_failures']['events'][0].update(restore_time_ns=24000,restore_route_update_delay_ns=60000)
        validate_topology(self.topology,self.protocol)

    def test_bounds_fields_and_single_failure(self):
        for count in (True,0,-1,100001,1.5):
            c=deepcopy(self.protocol);c['local_failover']['max_events']=count
            with self.subTest(count=count),self.assertRaises(ValueError):validate_options(c)
        for key in ('mode','max_events'):
            c=deepcopy(self.protocol);del c['local_failover'][key]
            with self.assertRaises(ValueError):validate_options(c)
        c=deepcopy(self.protocol);c['local_failover']['overhead_bytes']=0
        with self.assertRaises(ValueError):validate_options(c)
        c=deepcopy(self.protocol);c['link_failures']['events'].append(dict(src=129,dst=132,time_ns=5000,route_update_delay_ns=60000))
        with self.assertRaises(ValueError):validate_options(c)

    def test_single_NIC_plane_and_scale_limits(self):
        for change in ('NIC','plane','scale'):
            t=deepcopy(self.topology)
            if change=='NIC':t['links'].append(dict(src=0,dst=129,src_port=2,dst_port=1024))
            elif change=='plane':t['nodes'][132]['plane']=1
            else:t['nodes'][0]['kind']='tor'
            with self.subTest(change=change),self.assertRaises(ValueError):validate_topology(t,self.protocol)


if __name__=='__main__':unittest.main()
