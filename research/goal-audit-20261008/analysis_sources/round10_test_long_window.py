"""Regression checks for the time-window reconstruction, not simulator accuracy."""

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_long_window import integrate_bandwidth, sample_queue, time_edges


class TimeWindowTests(unittest.TestCase):
    def test_exact_window_no_extra_bin(self):
        edges = time_edges(5, 0, 1100)
        self.assertEqual(len(edges), 221)
        self.assertEqual(edges[-1], 1100)

    def test_partial_final_bin_and_clipping(self):
        packets = pd.DataFrame([dict(flow_kind="target", packet_start_us=-1, packet_end_us=4)])
        frame = integrate_bandwidth(packets, bin_us=1, start_us=0, end_us=2.5)
        target = frame[frame.flow_kind == "target"]
        np.testing.assert_allclose(target.bandwidth_gbps, [100, 100, 100])
        np.testing.assert_allclose(target.bin_width_us, [1, 1, 0.5])
        self.assertAlmostEqual(target.wire_bytes_in_bin.sum(), 31250)

    def test_empty_packets_are_zero_not_missing(self):
        packets = pd.DataFrame(columns=["flow_kind", "packet_start_us", "packet_end_us"])
        frame = integrate_bandwidth(packets, 0.25, 260, 280)
        self.assertEqual(len(frame), 5 * 80)
        self.assertEqual(frame.bandwidth_gbps.sum(), 0)

    def test_invalid_window(self):
        for values in [(0, 0, 1), (1, 2, 2), (1, 3, 2), (float("nan"), 0, 1)]:
            with self.assertRaises(ValueError):
                time_edges(*values)

    def test_exported_scope_and_wire_conservation(self):
        root = Path(__file__).resolve().parent
        packets = pd.read_csv(root / "long_window_packet_events.csv")
        for prefix, end in [("long_window", 1100), ("zoom_w1_w2", 280)]:
            bw = pd.read_csv(root / f"{prefix}_bandwidth_timeseries.csv")
            queue = pd.read_csv(root / f"{prefix}_queue_timeseries.csv")
            self.assertEqual(bw.bin_end_us.max(), end)
            self.assertTrue((queue.raw_max_time_us >= queue.raw_scope_start_us).all())
            self.assertTrue((queue.raw_max_time_us < queue.raw_scope_end_us).all())
            self.assertLessEqual(bw.groupby(["direction", "bin_start_us"]).bandwidth_gbps.sum().max(), 100.000001)
            clipped = np.maximum(0, np.minimum(packets.packet_end_us, end) - np.maximum(packets.packet_start_us, bw.bin_start_us.min()))
            self.assertAlmostEqual(bw.wire_bytes_in_bin.sum(), (clipped * 12500).sum(), places=3)

    def test_zoom_queue_peak_not_entire_run(self):
        queue = sample_queue("L2R", 8, 0.25, 100, 280)
        self.assertTrue(queue.raw_max_time_us.between(100, 280, inclusive="left").all())
        idle = sample_queue("L2R", 8, 0.25, 260, 280)
        self.assertEqual(idle.raw_max_queue_bytes.max(), 0)


if __name__ == "__main__":
    unittest.main()
