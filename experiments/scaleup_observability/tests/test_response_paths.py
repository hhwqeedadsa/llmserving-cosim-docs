"""Independent terminal timing, path continuity and byte-coverage checks."""
from copy import deepcopy
import unittest

from experiments.scaleup_observability.response_paths import reconstruct


class ResponsePaths(unittest.TestCase):
    def setUp(self):
        self.links={(1,0):dict(target=2,delay_ns=20),(2,0):dict(target=3,delay_ns=20),(3,0):dict(target=9,delay_ns=20)}
        self.points=[dict(node_id=n,port_id=0,start_ns=t,busy_until_ns=t+10,task_byte_range=[0,16],packet_bytes=32)
                     for n,t in [(1,10),(2,40),(3,70)]]
        self.transfer=dict(transfer_id=7,submit_ns=0,data_ready_ns=100,payload_bytes=16,committed_segments=1)

    def test_exact_last_hop_completion_and_decomposition(self):
        row,packets=reconstruct({5:self.points},self.transfer,self.links,{1},9)
        self.assertEqual((row['before_first_captured_response_ns'],row['captured_response_completion_span_ns'],row['data_latency_ns']),(10,90,100))
        self.assertEqual(packets[0]['reconstructed_arrival_ns'],100)
        self.assertEqual(row['owner_access_egress_ports'],1)

    def test_reject_early_next_hop_and_wrong_completion(self):
        points=deepcopy(self.points);points[1]['start_ns']=39
        with self.assertRaisesRegex(ValueError,'topology/serialization'):reconstruct({5:points},self.transfer,self.links,{1},9)
        with self.assertRaisesRegex(ValueError,'actual data-ready'):reconstruct({5:self.points},dict(self.transfer,data_ready_ns=101),self.links,{1},9)

    def test_reject_duplicate_byte_ranges_even_when_segment_count_matches(self):
        transfer=dict(self.transfer,payload_bytes=32,committed_segments=2)
        with self.assertRaisesRegex(ValueError,'gap, overlap or duplicate'):
            reconstruct({5:self.points,6:deepcopy(self.points)},transfer,self.links,{1},9)


if __name__=='__main__':unittest.main()
