"""Physical arrivals, GBN discards and useful delivery must not be conflated."""
from copy import deepcopy
import unittest

from moe_ub_cosim.scaleout_arrivals import add_range, reconstruct


class ArrivalsTest(unittest.TestCase):
    def inputs(self):
        columns='time_ns node port event packet_uid message_id ip_protocol sequence model_bytes ecn egress_queue_bytes data_payload_bytes'.split()
        def packet(t,node,event,seq,size=100,protocol=17):
            return [t,node,1,event,7,0,protocol,seq,size+42,0,0,size if protocol==17 else 0]
        events=[packet(1,0,0,0),packet(2,0,0,100),packet(3,1,1,100),
                packet(4,0,0,0),packet(5,1,1,0),packet(6,0,0,100),
                packet(7,1,1,100),packet(8,1,1,100),packet(9,0,1,200,0,252)]
        result=dict(native_network_executed=True,service={},passed=True,finish_ns=10,gpu_count=2,
            rdma_parameters=dict(hosts=[dict(node=n,attributes=dict(L2BackToZero=False)) for n in (0,1)]),
            messages=[dict(id=0,src=0,dst=1,bytes=200,plane=0,source_data_packets=4,
                source_data_payload_bytes=400,receiver_contiguous_bytes=200,receive_ready_ns=7,ack_ns=9)],
            packet_capture=dict(complete=True,omitted_events=0,
                selection=dict(start_ns=0,end_ns=11,nodes=[]),columns=columns,events=events,
                event_codes=dict(tx=0,rx=1,drop=2),protocol_codes=dict(data=17,ack=252,nack=253)))
        waves=[dict(graph_id='g0.w0',group_index=0,decode_step=0,routes=[dict(layer=0,source_layer_id=3)],
            messages=[dict(id=0,src=0,dst=1,bytes=200,plane=0,layer=0,phase='dispatch')])]
        return result,waves

    def test_ooo_duplicate_and_uid_lineage(self):
        r,w=self.inputs();summary,m,_,packets,curve=reconstruct(r,w,'g0.w0',0,'dispatch')
        early=next(row for row in curve if row['time_ns']==3)
        self.assertEqual((early['unique_arrival_bytes'],early['accepted_bytes']),(100,0))
        self.assertEqual((m[0]['tx_payload_bytes'],m[0]['tx_unique_bytes']),(400,200))
        self.assertEqual((m[0]['rx_payload_bytes'],m[0]['accepted_bytes']),(400,200))
        self.assertEqual((m[0]['out_of_order_discard_packets'],m[0]['duplicate_rx_packets']),(1,1))
        self.assertEqual(summary['slice']['data_ready_ns'],7)
        self.assertEqual(sum(p['accepted_delta_bytes'] for p in packets),200)

    def test_partial_capture_and_bad_final_state_rejected(self):
        r,w=self.inputs()
        for patch in (dict(complete=False),dict(omitted_events=1),
                      dict(selection=dict(start_ns=0,end_ns=9,nodes=[])),
                      dict(selection=dict(start_ns=0,end_ns=11,nodes=[0]))):
            x=deepcopy(r);x['packet_capture'].update(patch)
            with self.assertRaises(ValueError):reconstruct(x,w,'g0.w0',0,'dispatch')
        for key,value in (('receiver_contiguous_bytes',100),('receive_ready_ns',6),('source_data_packets',3)):
            x=deepcopy(r);x['messages'][0][key]=value
            with self.assertRaises(ValueError):reconstruct(x,w,'g0.w0',0,'dispatch')

    def test_interval_overlap(self):
        ranges=[]
        self.assertEqual(add_range(ranges,5,15),10)
        self.assertEqual(add_range(ranges,0,10),5)
        self.assertEqual(add_range(ranges,3,12),0)
        self.assertEqual(ranges,[(0,15)])

    def test_confirmation_time_is_independent_of_summary(self):
        r,w=self.inputs()
        for stamp in (1,8,10,None):
            changed=deepcopy(r);changed['messages'][0]['ack_ns']=stamp
            with self.subTest(stamp=stamp),self.assertRaisesRegex(ValueError,'confirmation time'):
                reconstruct(changed,w,'g0.w0',0,'dispatch')
        summary,m,*_=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertEqual(summary['slice']['source_confirmed_ns'],9)
        self.assertEqual(m[0]['ack_ns'],9)

    def test_complete_data_without_final_feedback_stays_unconfirmed(self):
        r,w=self.inputs();r['passed']=False
        r['messages'][0]['ack_ns']=None
        r['packet_capture']['events'].pop()
        summary,*_=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertTrue(summary['slice']['complete'])
        self.assertEqual(summary['slice']['confirmed_bytes'],0)
        self.assertIsNone(summary['slice']['source_confirmed_ns'])

    def test_duplicate_feedback_keeps_first_completion_and_equal_times_work(self):
        r,w=self.inputs()
        ack=deepcopy(r['packet_capture']['events'][-1]);ack[0]=10
        r['packet_capture']['events'].append(ack)
        summary,*_=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertEqual(summary['slice']['source_confirmed_ns'],9)
        r,w=self.inputs()
        r['packet_capture']['events'][-1][0]=8
        r['messages'][0]['ack_ns']=8
        # The complete ACK can follow a destination event at the same ns.
        summary,*_=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertEqual(summary['slice']['source_confirmed_ns'],8)

    def test_feedback_cannot_confirm_unreceived_data(self):
        r,w=self.inputs()
        ack=r['packet_capture']['events'][-1]
        ack[0]=3
        r['packet_capture']['events'].insert(3,r['packet_capture']['events'].pop())
        r['messages'][0]['ack_ns']=3
        with self.assertRaisesRegex(ValueError,'precedes receiver acceptance'):
            reconstruct(r,w,'g0.w0',0,'dispatch')

    def test_failed_service_keeps_missing_bytes(self):
        r,w=self.inputs();r.update(passed=False,finish_ns=5)
        r['packet_capture']['events']=r['packet_capture']['events'][:3]
        r['messages'][0].update(source_data_packets=2,source_data_payload_bytes=200,
            receiver_contiguous_bytes=0,receive_ready_ns=None,ack_ns=None)
        summary,*_=reconstruct(r,w,'g0.w0',0,'dispatch')
        self.assertFalse(summary['slice']['complete'])
        self.assertEqual(summary['slice']['unique_arrival_bytes'],100)
        self.assertEqual(summary['slice']['accepted_bytes'],0)
        self.assertIsNone(summary['slice']['data_ready_ns'])


if __name__=='__main__':unittest.main()
