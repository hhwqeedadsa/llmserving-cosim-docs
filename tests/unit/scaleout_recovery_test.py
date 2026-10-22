"""Closed recovery configuration: native baseline and declared bounded extension."""
import json
from pathlib import Path
import unittest
from copy import deepcopy
from moe_ub_cosim.scaleout_rdma import validate_protocol
from moe_ub_cosim.scaleout_recovery import audit

def complete():
    return dict(passed=True,finish_ns=5,messages=[dict(id=8,src=0,bytes=1000,source_unique_data_payload_bytes=1000,
        ack_callbacks=1,ack_ns=5,sender_qp_present=False,sender_resource_released_ns=5)],
        recovery=dict(config=dict(mode='go_back_n',initial_rto_ns=10,max_rto_ns=20,max_retries=1,max_events=100),
            failed=False,inflight_packets=0,finish_waiting_for_drain=False,duplicate_ack_responses=0,
            states=[dict(id=8,source=0,acknowledged_bytes=1000,highest_sent_bytes=1000,retries_since_progress=0,
                timeout_retransmissions=0,timeout_expiries=0,current_rto_ns=10,deadline_ns=-1,state='complete')],
            complete=True,observations=2,omitted_events=0,events=[[0,0,0,8,0,1000,0,10,10],[5,3,0,8,1000,1000,0,10,-1]],
            columns=['time_ns','event','source','message_id','acknowledged_bytes','highest_sent_bytes','retries_since_progress','rto_ns','deadline_ns'],
            receiver_events=[]))

class RecoveryConfigTest(unittest.TestCase):
    def test_complete_history_and_terminal_corruption(self):
        self.assertTrue(audit(complete())['reconstructed'])
        for key,value in [('acknowledged_bytes',1001),('timeout_retransmissions',1),('state','retry_exhausted')]:
            d=complete();d['recovery']['states'][0][key]=value
            with self.assertRaises(ValueError):audit(d)
        d=complete();d['recovery']['events'][1][0]=11;d['finish_ns']=11;d['messages'][0]['ack_ns']=11
        with self.assertRaisesRegex(ValueError,'progress'):audit(d)

    def test_failure_has_no_success_or_early_resource_release(self):
        d=complete();d.update(passed=False,finish_ns=30,failure=dict(reason='recovery_retry_exhausted',message_id=8,time_ns=30))
        d['messages'][0].update(ack_callbacks=0,ack_ns=None,sender_qp_present=True,sender_resource_released_ns=None)
        r=d['recovery'];r['failed']=True;r['states'][0].update(acknowledged_bytes=0,retries_since_progress=1,
            timeout_retransmissions=1,timeout_expiries=2,current_rto_ns=20,state='retry_exhausted')
        r.update(observations=3,events=[[0,0,0,8,0,1000,0,10,10],[10,2,0,8,0,1000,1,20,30],[30,4,0,8,0,1000,1,20,-1]])
        self.assertEqual(audit(d)['failed_ids'],[8])
        for key,value in [('ack_callbacks',1),('sender_resource_released_ns',30),('sender_qp_present',False)]:
            bad=deepcopy(d);bad['messages'][0][key]=value
            with self.assertRaises(ValueError):audit(bad)

    def test_truncated_capture_and_idle_source_tail(self):
        d=complete();r=d['recovery'];r.update(complete=False,omitted_events=1,events=r['events'][:1])
        self.assertFalse(audit(d)['reconstructed'])
        r['complete']=True
        with self.assertRaises(ValueError):audit(d)
        d=complete();d.update(passed=False,finish_ns=5);d['messages'][0].update(ack_callbacks=0,ack_ns=None,sender_qp_present=True)
        d['recovery']['states'][0].update(acknowledged_bytes=0,state='waiting_ack',deadline_ns=10)
        r=d['recovery'];r.update(observations=1,events=[[0,0,0,8,0,100,0,10,10]])
        self.assertTrue(audit(d)['reconstructed']) # Sent high-water can grow between bounded protocol transitions.

    def test_modes_and_bounds(self):
        base=json.loads((Path(__file__).resolve().parents[2]/'examples/scaleout_rdma/rdma.json').read_text())
        valid=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=2,max_events=100000)
        for config in (dict(mode='native'),valid,dict(valid,max_events=0)):
            validate_protocol(dict(base,recovery=config))
        for key,value in [('initial_rto_ns',True),('initial_rto_ns',0),('initial_rto_ns',5000001),
                          ('max_rto_ns',39999),('max_retries',0),('max_retries',17),('max_events',100001),
                          ('max_events',True),('mode','other'),('extra',0)]:
            with self.subTest(key=key,value=value):
                with self.assertRaises(ValueError):validate_protocol(dict(base,recovery=dict(valid,**{key:value})))
        for config in (dict(mode='native',max_retries=2),None,{},True):
            with self.assertRaises(ValueError):validate_protocol(dict(base,recovery=config))

    def test_old_ack_between_native_pacing_intervals(self):
        d=complete();d.update(finish_ns=25);d['messages'][0]['ack_ns']=25
        r=d['recovery'];r.update(observations=5,events=[
            [0,0,0,8,0,500,0,10,10],
            [5,1,0,8,500,500,0,10,-1],
            [15,6,0,8,500,500,0,10,-1],
            [20,0,0,8,500,1000,0,10,30],
            [25,3,0,8,1000,1000,0,10,-1]])
        self.assertTrue(audit(d)['reconstructed'])
        paused=deepcopy(d);paused.update(passed=False,finish_ns=15)
        paused['messages'][0].update(ack_callbacks=0,ack_ns=None,sender_qp_present=True,
            sender_resource_released_ns=None,source_unique_data_payload_bytes=500)
        paused['recovery'].update(observations=3,events=r['events'][:3])
        paused['recovery']['states'][0].update(acknowledged_bytes=500,highest_sent_bytes=500,state='waiting_ack')
        self.assertTrue(audit(paused)['reconstructed'])
        r['events'][2][-1]=25
        with self.assertRaisesRegex(ValueError,'feedback'):audit(d)

if __name__=='__main__':unittest.main()
