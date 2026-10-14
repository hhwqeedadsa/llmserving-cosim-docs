"""Hand-computable service/queue intervals for researcher preparation."""
import unittest
from ..analysis.diagnostic_facts import LocalState,IX

class ExactLocalTest(unittest.TestCase):
    def test_simultaneous_move_is_continuous_without_double_counting(self):
        s=LocalState((1,2),400000000000)
        s.event(0,'initial',dict(voq_bytes=0,fifo_bytes=0))
        s.event(100,'voq',dict(bytes=100))
        s.event(200,'voq',dict(bytes=0));s.event(200,'fifo',dict(bytes=100))
        s.event(500,'fifo',dict(bytes=0));s.event(1000,'final',{})
        self.assertEqual(len(s.episodes),1)
        self.assertEqual((s.episodes[0]['start_ns'],s.episodes[0]['end_ns']),(100,500))
        self.assertEqual(s.windows[0][IX['voq_byte_ns']],10000)
        self.assertEqual(s.windows[0][IX['fifo_byte_ns']],30000)
        self.assertEqual(s.windows[0][IX['waiting_ns']],400)

    def test_overlapping_control_service_uses_union_and_zero_ticks(self):
        s=LocalState((1,2),400000000000)
        s.event(0,'initial',dict(voq_bytes=10,fifo_bytes=0))
        for t,duration in [(100,700),(300,200),(500,0)]:
            s.event(t,'tx',dict(packet_bytes=100,completion_delay_ns=duration,rate_bps=400000000000))
        s.event(1000,'final',{})
        self.assertEqual(s.windows[0][IX['busy_ns']],700)
        self.assertEqual(s.windows[0][IX['serial_work_ns']],900)
        self.assertEqual(s.windows[0][IX['queued_busy_ns']],700)

    def test_partial_window_does_not_extend_coverage(self):
        s=LocalState((1,2),1);s.event(0,'initial',dict(voq_bytes=1,fifo_bytes=0))
        s.event(1250,'final',{})
        self.assertEqual([r[IX['waiting_ns']] for r in s.windows.values()],[1000,250])
        self.assertEqual(s.episodes[0]['end_ns'],1250)

    def test_fixed_phase_histogram_samples_settled_state(self):
        s=LocalState((1,2),1);s.event(0,'initial',dict(voq_bytes=0,fifo_bytes=0))
        s.event(1024,'voq',dict(bytes=8192));s.event(2048,'final',{})
        self.assertEqual(dict(s.histogram),{0:1,14:1})

if __name__=='__main__':unittest.main()
