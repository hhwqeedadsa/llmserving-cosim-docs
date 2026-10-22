"""Time decomposition must preserve incomplete stages and protocol boundaries."""
import unittest
from moe_ub_cosim.scaleout_metrics import phase_intervals, network_summary


class MetricsTest(unittest.TestCase):
    def flow(self):
        return dict(bytes=100,ready_ns=0,admission_granted_ns=5,first_tx_ns=10,
            receive_ready_ns=20,ack_ns=30,sender_resource_released_ns=30,
            sender_progress=dict(snd_una_bytes=100,snd_nxt_bytes=100))

    def test_disjoint_and_incomplete(self):
        f=self.flow();r=phase_intervals(f,40)
        self.assertEqual(sum(r['durations_ns'].values()),30);self.assertIsNone(r['open_stage'])
        f.update(ack_ns=None,sender_resource_released_ns=None)
        f['sender_progress']['snd_una_bytes']=0
        r=phase_intervals(f,40)
        self.assertFalse(r['complete']);self.assertIsNone(r['durations_ns']['confirmation_tail'])
        self.assertEqual(r['open_stage'],dict(name='confirmation_tail',elapsed_ns=20))

    def test_invalid_boundaries_and_progress(self):
        for key,value in [('ack_ns',19),('first_tx_ns',None),('receive_ready_ns',41)]:
            f=self.flow();f[key]=value
            with self.assertRaises(ValueError):phase_intervals(f,40)
        f=self.flow();f['sender_progress']['snd_una_bytes']=101
        with self.assertRaises(ValueError):phase_intervals(f,40)
        self.assertFalse(phase_intervals({},40)['available'])

    def test_queue_bounds(self):
        r=dict(passed=True,finish_ns=50,queue_metrics_scope='test',ports=[dict(queue_metrics=
            dict(area_byte_ns=1000,peak_bytes=100,final_bytes=0,changes=4))])
        self.assertEqual(network_summary(r)['area_byte_ns'],1000)
        r['ports'][0]['queue_metrics']['final_bytes']=1
        with self.assertRaises(ValueError):network_summary(r)
        r['passed']=False;self.assertEqual(network_summary(r)['final_bytes'],1)


if __name__=='__main__':unittest.main()
