"""Independent expected state/byte checks for the bounded RC safety reference."""
import unittest

from moe_ub_cosim.rc_reference import (PSN_MAX, WORD_MAX, Request, Response,
                                      RcRequester, RcResponder, UnsupportedTransition)


class ReferenceTest(unittest.TestCase):
    def pair(self, value=41, slots=1, depth=1):
        return RcRequester(atomic_depth=depth), RcResponder(value.to_bytes(8,'little')+bytes(1024),replay_slots=slots)

    def test_segment_visibility_and_source_lifetime(self):
        s,r=self.pair()
        data=bytes(range(256))*2+b'end'
        s.post('write',8,data=data)
        packets=s.send()
        self.assertEqual([p.opcode for p in packets],['WRITE_FIRST','WRITE_MIDDLE','WRITE_LAST'])
        self.assertEqual([p.ack_requested for p in packets],[False,False,True])
        self.assertIsNone(r.receive(packets[0]))
        self.assertEqual(bytes(r.memory[8:264]),data[:256])
        self.assertEqual(s.snapshot()['source_held'],[0])
        self.assertIsNone(r.receive(packets[1]))
        ack=r.receive(packets[2])
        self.assertEqual(ack,Response(2,'ACK'))
        self.assertEqual(s.snapshot()['source_held'],[0])
        s.receive(ack)
        self.assertEqual(s.snapshot()['source_held'],[])
        self.assertEqual(bytes(r.memory[8:8+len(data)]),data)

    def test_atomic_old_value_and_u64_wrap(self):
        s,r=self.pair(WORD_MAX-1)
        s.post('fetch_add',0,addend=5)
        packet,=s.send()
        self.assertEqual(packet.payload,b'')
        response=r.receive(packet)
        self.assertEqual(response,Response(0,'ATOMIC_ACK',WORD_MAX-1))
        self.assertEqual(int.from_bytes(r.memory[:8],'little'),3)
        self.assertEqual(s.snapshot()['atomic_credit'],0)
        s.receive(response)
        self.assertEqual(s.snapshot()['operations'][0]['old_value'],WORD_MAX-1)
        self.assertEqual(s.snapshot()['atomic_credit'],1)

    def test_replay_is_old_value_not_second_add(self):
        s,r=self.pair()
        s.post('fetch_add',0,addend=5)
        packet,=s.send()
        original=r.receive(packet)
        duplicate=r.receive(Request(0,'FETCH_ADD',address=0,key=1,addend=99))
        self.assertEqual(original,duplicate)
        self.assertEqual(len(r.effects),1)
        self.assertEqual(int.from_bytes(r.memory[:8],'little'),46)
        s.receive(duplicate)
        self.assertEqual(s.retransmit(),())

    def test_atomic_reply_does_not_require_another_ack(self):
        s,r=self.pair()
        s.post('fetch_add',0,addend=1)
        request,=s.send()
        s.receive(r.receive(request))
        self.assertEqual(s.snapshot()['source_held'],[])
        self.assertEqual(s.send(),())

    def test_credit_and_fence(self):
        s,r=self.pair()
        s.post('fetch_add',0,addend=1)
        s.post('fetch_add',0,addend=2)
        a,=s.send()
        self.assertEqual(s.send(),())
        s.receive(r.receive(a))
        b,=s.send()
        s.post('write',8,data=b'x',fence=True)
        self.assertEqual(s.send(),())
        s.receive(r.receive(b))
        self.assertEqual(len(s.send()),1)

    def test_plain_ack_at_atomic_never_invents_old_value(self):
        s,r=self.pair()
        s.post('write',8,data=b'x')
        s.post('fetch_add',0,addend=5)
        write,=s.send();atomic,=s.send()
        r.receive(write);r.receive(atomic)  # Both responses deliberately unreceived.
        cumulative=r.receive(write)
        self.assertEqual(cumulative,Response(1,'ACK'))
        with self.assertRaises(UnsupportedTransition):s.receive(cumulative)
        state=s.snapshot()
        self.assertTrue(state['operations'][0]['completed'])
        self.assertFalse(state['operations'][1]['completed'])
        self.assertIsNone(state['operations'][1]['old_value'])
        self.assertEqual(state['source_held'],[1])
        self.assertEqual(state['atomic_credit'],0)
        s.receive(r.receive(atomic))
        self.assertEqual(s.snapshot()['operations'][1]['old_value'],41)

    def test_later_ack_does_not_skip_pending_atomic(self):
        s,r=self.pair()
        s.post('fetch_add',0,addend=5);s.post('write',8,data=b'x')
        atomic,=s.send();write,=s.send()
        r.receive(atomic)
        self.assertEqual(s.receive(r.receive(write)),'retry_required')
        self.assertEqual(s.snapshot()['source_held'],[0,1])

    def test_gap_nak_suppression_and_prefix_reset(self):
        s,r=self.pair()
        s.post('write',8,data=b'x'*600)
        a,b,c=s.send()
        self.assertEqual(r.receive(c),Response(0,'SEQ_NAK'))
        self.assertIsNone(r.receive(b))
        self.assertIsNone(r.receive(a))
        self.assertEqual(r.receive(c),Response(1,'SEQ_NAK'))
        self.assertEqual(len(r.effects),1)

    def test_evicted_atomic_replay_drops_without_effect(self):
        r=RcResponder(bytes(8),replay_slots=1)
        r.receive(Request(0,'FETCH_ADD',address=0,key=1,addend=1))
        r.receive(Request(1,'FETCH_ADD',address=0,key=1,addend=2))
        self.assertIsNone(r.receive(Request(0,'FETCH_ADD',address=0,key=1,addend=1)))
        self.assertEqual(len(r.effects),2)
        self.assertEqual(int.from_bytes(r.memory,'little'),3)

    def test_periodic_ack_exact_postincrement_boundary(self):
        s=RcRequester(mtu=256)
        s.post('write',0,data=bytes(65536))
        packets=s.send()
        self.assertEqual([i for i,p in enumerate(packets) if p.ack_requested],[65,131,197,255])

    def test_invalid_inputs_and_unsupported_wrap_preserve_state(self):
        with self.assertRaises(ValueError):RcRequester(atomic_depth=True)
        with self.assertRaises(ValueError):RcResponder(bytes(8),mtu=2000)
        with self.assertRaises(ValueError):Response(0,'ACK',1)
        s=RcRequester(initial_psn=PSN_MAX)
        s.post('fetch_add',0,addend=1)
        with self.assertRaises(UnsupportedTransition):s.send()
        self.assertEqual(s.snapshot()['source_held'],[])
        r=RcResponder(bytes(8))
        with self.assertRaises(UnsupportedTransition):r.receive(Request(0,'FETCH_ADD',address=0,key=2,addend=1))
        self.assertEqual(r.effects,[])


if __name__=='__main__':unittest.main()
