"""Hand-computable checks for mechanism screening, not benefit assertions."""
import unittest
from ..analysis.mechanism_screen import classify, ports, quantile_time, sequences


RULE=dict(busy_proxy_min_fraction=.8,low_service_proxy_max_fraction=.2)


class MechanismScreen(unittest.TestCase):
    def test_service_is_a_start_byte_proxy(self):
        self.assertEqual(classify(1,40000,1000,400_000_000_000,RULE),'waiting_high_service')
        self.assertEqual(classify(1,10000,1000,400_000_000_000,RULE),'waiting_low_service')
        self.assertEqual(classify(0,10000,1000,400_000_000_000,RULE),'no_positive_wait')
        self.assertEqual(classify(1,20000,1000,400_000_000_000,RULE),'waiting_intermediate_service')

    def test_gap_cannot_be_filled_as_continuous(self):
        rows=[dict(start_ns=a,end_ns=a+1000,stratum='waiting_low_service',
                   voq_byte_ns=1,fifo_byte_ns=0,packet_bytes=0) for a in (0,1000,3000)]
        self.assertEqual([g['positive_windows'] for g in sequences(rows,RULE)],[2,1])
        rows[1]['stratum']='waiting_high_service'
        self.assertEqual(len(sequences(rows,RULE)),3)

    def test_byte_quantile_includes_unequal_work(self):
        rows=[dict(end_ns=1000,packet_bytes=9),dict(end_ns=2000,packet_bytes=1)]
        self.assertEqual(quantile_time(rows,.9),1000)
        self.assertEqual(quantile_time(rows,1),2000)
        self.assertIsNone(quantile_time([],1))

    def test_planes_are_derived_from_physical_topology(self):
        from moe_ub_cosim.fabric import generate
        fabric=dict(schema_version='moe-ub-fabric/1',shape=dict(servers=2,chips_per_server=1,
            dies_per_chip=2,planes=2,l2_per_plane=1),groups=[dict(group_id='g',rank_ids=[0,1,2,3],rank_to_ub_node=[0,1,2,3])],
            budget=dict(max_nodes=20,max_links=30,max_route_rows=100,max_channels=100))
        topology,*_=generate(fabric,dict(rate_bps=400000000000,delay_ns=20,allocation_ns=1,forward_ns=0))
        cfg=dict(fabric=fabric,topology=topology)
        public=dict(ports=[dict(switch_id=s,port_id=p) for s,p in ((4,0),(5,0),(4,2),(8,1))])
        r=ports(cfg,public)
        self.assertEqual(r[4,0]['peer_group'],r[5,0]['peer_group'])
        self.assertEqual([r[4,0]['plane'],r[5,0]['plane']],[0,1])
        self.assertEqual(r[4,2]['role'],'inter_switch')
        self.assertEqual(r[8,1]['peer_group'],['l2_down',0,1])


if __name__=='__main__':unittest.main()
