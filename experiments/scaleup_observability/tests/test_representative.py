"""Exact streamed quantiles and port-window/topology interpretation."""
import unittest

from experiments.scaleup_observability.representative import observation
from experiments.scaleup_observability.representative_analysis import histogram_summary, fully_inside, locality_groups


class Representative(unittest.TestCase):
    def test_weighted_nearest_rank_quantiles(self):
        self.assertEqual(histogram_summary({0:2,4:1,10:1}),
            dict(count=4,min=0,max=10,mean=3.5,median=0,p95=10,p99=10))
        value=histogram_summary({1:95,100:4,1000:1})
        self.assertEqual((value['median'],value['p95'],value['p99']),(1,1,100))
        with self.assertRaisesRegex(ValueError,'histogram'):histogram_summary({})

    def test_only_wholly_contained_windows_count_as_hits(self):
        def row(a,b):return dict(local_window_start_ns=a,local_window_end_ns=b)
        self.assertTrue(fully_inside(row(10,20),(10,20)))
        self.assertFalse(fully_inside(row(9,11),(10,20)))
        self.assertFalse(fully_inside(row(19,21),(10,20)))
        self.assertFalse(fully_inside(row(20,30),(10,20)))

    def test_locality_uses_links_not_rank_number(self):
        network=dict(topology=dict(nodes=[dict(node_id=i,kind=k) for i,k in [(0,'DEVICE'),(20,'DEVICE'),(1,'DEVICE'),(8,'SWITCH'),(9,'SWITCH')]],
            links=[dict(src=0,dst=8),dict(src=8,dst=20),dict(src=1,dst=9)]))
        groups=locality_groups(network,[0,20,1])
        self.assertTrue(groups[0]&groups[1]);self.assertFalse(groups[0]&groups[2])

    def test_equal_three_port_observation(self):
        value=observation(dict(l1_up=(768,16,224000000000),l2_down=(1104,0,224000000000),access_down=(768,0,224000000000)))
        self.assertEqual(sum(len(s['port_ids']) for s in value['switches']),3)
        self.assertEqual({s['period_ns'] for s in value['switches']},{1000})
        self.assertEqual(value['recording']['detail_ranges'],[])
        self.assertEqual(len([c for c in value['clocks'] if c['device_type']=='rank']),128)


if __name__=='__main__':unittest.main()
