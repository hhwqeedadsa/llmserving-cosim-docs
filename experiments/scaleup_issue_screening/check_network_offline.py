"""Synthetic checks for offline bounds; no simulation or research-gain assertions."""
import unittest

from .network_offline import transfer_bounds


class SourceBounds(unittest.TestCase):
    def test_delivery_and_protocol_retirement_are_different(self):
        t=dict(operation_id='x',src_rank=0,dst_rank=1)
        endpoints={('x',0):dict(start_local_ns=0,end_local_ns=20),
                   ('x',1):dict(start_local_ns=0,end_local_ns=40)}
        a=dict(send_start_ns=5,data_ready_ns=20,transaction_done_ns=50)
        self.assertTrue(all(transfer_bounds(t,a,endpoints).values()))
        a['data_ready_ns']=21
        self.assertEqual(transfer_bounds(t,a,endpoints),
            dict(source_start=True,destination_end=True,source_end=False))

    def test_all_counterexamples_are_exposed(self):
        t=dict(operation_id='x',src_rank=0,dst_rank=1)
        endpoints={('x',0):dict(start_local_ns=5,end_local_ns=10),
                   ('x',1):dict(start_local_ns=0,end_local_ns=20)}
        a=dict(send_start_ns=4,data_ready_ns=21)
        self.assertFalse(any(transfer_bounds(t,a,endpoints).values()))


if __name__=='__main__':unittest.main()
