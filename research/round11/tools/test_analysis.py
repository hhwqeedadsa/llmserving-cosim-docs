import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from analyze import bandwidth, sample_queue, ROOT, PKG

def packets(rows):
    return pd.DataFrame(rows,columns=["start_us","end_us","direction","group"])

class AnalysisTests(unittest.TestCase):
    def test_bin_overlap_and_conservation(self):
        b=bandwidth(packets([(0.25,1.25,"L2R","G0")]),100,width=1,begin=0,end=2)
        z=b[(b.direction=="L2R")&(b.group=="G0")]
        np.testing.assert_allclose(z.gbps,[75,25])
        self.assertAlmostEqual(b.wire_bytes.sum(),12500)

    def test_partial_final_bin(self):
        b=bandwidth(packets([(0,2.5,"L2R","G0")]),100,width=1,begin=0,end=2.5)
        z=b[(b.direction=="L2R")&(b.group=="G0")]
        np.testing.assert_allclose(z.gbps,[100,100,100])
        self.assertEqual(b.end_us.max(),2.5)

    def test_window_clipping_and_direction(self):
        b=bandwidth(packets([(0,4,"R2L","G1"),(5,6,"L2R","G0")]),200,width=.5,begin=1,end=2)
        self.assertAlmostEqual(b.wire_bytes.sum(),25000)
        self.assertEqual(b[b.direction=="L2R"].gbps.sum(),0)

    def test_empty_groups(self):
        b=bandwidth(packets([]),100,width=.25,begin=0,end=1)
        self.assertEqual(len(b),32)
        self.assertEqual(b.gbps.sum(),0)

    def test_invalid_bounds(self):
        with self.assertRaises(ValueError):
            bandwidth(packets([]),100,width=0)
        with self.assertRaises(ValueError):
            bandwidth(packets([]),100,begin=2,end=1)

    def test_queue_carries_pre_window_state(self):
        q=pd.DataFrame([(0,100,"L2R"),(2,0,"L2R"),(0,0,"R2L")],
                       columns=["time_us","bytes","direction"])
        z=sample_queue(q,width=.5,begin=1,end=3)
        np.testing.assert_allclose(z[z.direction=="L2R"].kib,[100/1024,100/1024,0,0])

    def test_fixed_controls_from_actual_inputs(self):
        a=PKG/"cases/no_kv_100"; b=PKG/"cases/kv_100"; c=PKG/"cases/kv_200"
        for name in ["node.csv","routing_table.csv","network_attribute.txt"]:
            self.assertEqual((a/name).read_bytes(),(b/name).read_bytes())
            self.assertEqual((b/name).read_bytes(),(c/name).read_bytes())
        self.assertEqual((a/"topology.csv").read_bytes(),(b/"topology.csv").read_bytes())
        tb=pd.read_csv(b/"topology.csv"); tc=pd.read_csv(c/"topology.csv")
        changed=(tb!=tc).stack()
        self.assertEqual(int(changed.sum()),1)
        self.assertEqual(tb.loc[8,"bandwidth"],"100Gbps")
        self.assertEqual(tc.loc[8,"bandwidth"],"200Gbps")
        self.assertEqual((b/"traffic.csv").read_bytes(),(c/"traffic.csv").read_bytes())
        ta=pd.read_csv(a/"traffic.csv"); tbg=pd.read_csv(b/"traffic.csv")
        pd.testing.assert_frame_equal(ta,tbg.iloc[:240].reset_index(drop=True))

    def test_all_generated_results(self):
        summary=pd.read_csv(ROOT/"analysis/summary.csv").set_index("case_id")
        self.assertEqual(int(summary.tasks.sum()),820)
        self.assertEqual(int(summary.packets.sum()),59600)
        for name in summary.index:
            d=ROOT/"analysis"/name
            p=pd.read_csv(d/"packets.csv"); t=pd.read_csv(d/"tasks.csv")
            pd.testing.assert_series_equal(p.groupby("task_id").payload_bytes.sum().sort_index(),
                t.set_index("task_id").payload_bytes.sort_index(),check_names=False)
            b=pd.read_csv(d/"bandwidth_5us.csv")
            self.assertLess(abs(b.wire_bytes.sum()-p.wire_bytes.sum()),.02)
            self.assertLessEqual(b.groupby(["direction","center_us"]).gbps.sum().max(),
                                 summary.loc[name,"core_gbps"]+1e-5)
            self.assertLess(t.last_ack_us.max(),8400)
        e=pd.read_csv(ROOT/"analysis/kv_100/events.csv")
        compute=e[e.type=="compute"]
        self.assertEqual(len(compute),180)
        self.assertTrue(set(np.round(compute.end_us-compute.start_us,3)).issubset({36.33,2.549,456.737}))

if __name__=="__main__":
    unittest.main(verbosity=2)
