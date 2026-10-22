"""Reject resource ownership corruption, early reuse and misleading capture claims."""
from copy import deepcopy
import unittest

from moe_ub_cosim.scaleout_admission import audit, validate_options


def completed():
    return dict(passed=True,messages=[dict(id=8,src=0,ready_ns=0,admission_granted_ns=0,qp_registered_ns=0,
        receive_ready_ns=10,ack_ns=20,sender_resource_released_ns=20)],
        admission=dict(policy='fifo',max_active_per_source=1,decision_ns=0,sources=[dict(src=0,
            active_ids=[],pending_ids=[],decision_pending=False,native_send_qps=0)]),
        lifecycle_capture=dict(complete=True,observations=6,omitted_events=0,
            columns=['time_ns','event','message_id','source','active','pending','software_limit','native_send_qps'],
            event_codes=dict(request=0,grant=1,qp_registered=2,data_ready=3,ack_complete=4,
                qp_map_removed_credit_release=5,decision_start=6),
            events=[[0,0,8,0,0,1,1,0],[0,1,8,0,1,0,1,0],[0,2,8,0,1,0,1,1],
                    [10,3,8,0,1,0,1,1],[20,4,8,0,1,0,1,1],[20,5,8,0,0,0,1,0]]))


class AdmissionAuditTest(unittest.TestCase):
    def test_closed_options_and_numeric_types(self):
        for policy in ('fifo','destination_round_robin','passthrough'):
            a=dict(policy=policy,max_active_per_source=0 if policy=='passthrough' else 2,decision_ns=0)
            validate_options(dict(admission=a,lifecycle_capture=dict(max_events=100)))
            for key,value in [('policy','unknown'),('max_active_per_source',True),('decision_ns',True),
                              ('decision_ns',-1),('extra',0)]:
                with self.subTest(policy=policy,key=key):
                    with self.assertRaises(ValueError):validate_options(dict(admission=dict(a,**{key:value})))
        for a in (dict(policy='fifo',max_active_per_source=0,decision_ns=0),
                  dict(policy='passthrough',max_active_per_source=1,decision_ns=0),
                  dict(policy='passthrough',max_active_per_source=0,decision_ns=1)):
            with self.assertRaises(ValueError):validate_options(dict(admission=a))

    def test_success_and_independent_count_reconstruction(self):
        r=audit(completed())
        self.assertTrue(r['reconstructed']);self.assertEqual(r['releases'],1)
        for row,column,value in [(3,4,0),(2,7,0),(0,3,1),(5,2,99),(4,0,9)]:
            d=completed();d['lifecycle_capture']['events'][row][column]=value
            with self.subTest(row=row,column=column):
                with self.assertRaises(ValueError):audit(d)

    def test_byte_config_and_ownership(self):
        a=dict(policy='fifo',max_active_per_source=2,decision_ns=0,max_active_bytes_per_source=100)
        validate_options(dict(admission=a))
        for value in (True,-1,1073741825):
            with self.assertRaises(ValueError):validate_options(dict(admission=dict(a,max_active_bytes_per_source=value)))
        with self.assertRaises(ValueError):validate_options(dict(admission=dict(a,policy='passthrough',max_active_per_source=0)))
        d=completed();d['messages'][0]['bytes']=100
        d['admission']['max_active_bytes_per_source']=100
        d['admission']['sources'][0].update(active_bytes=0,pending_bytes=0)
        self.assertEqual(audit(d)['peak_active_logical_bytes']['0'],100)
        d['admission']['max_active_bytes_per_source']=99
        with self.assertRaises(ValueError):audit(d)

    def test_data_ready_does_not_release_owner(self):
        d=completed();cap=d['lifecycle_capture']
        cap['events'][4][1]=5
        with self.assertRaisesRegex(ValueError,'premature'):audit(d)

    def test_capture_truncation_does_not_claim_reconstruction(self):
        d=completed();cap=d['lifecycle_capture']
        cap.update(complete=False,omitted_events=3,events=cap['events'][:3])
        self.assertFalse(audit(d)['reconstructed'])
        cap['complete']=True
        with self.assertRaisesRegex(ValueError,'complete lifecycle'):audit(d)

    def test_absent_capture_still_checks_final_resources(self):
        d=completed();d.pop('lifecycle_capture')
        self.assertEqual(audit(d)['reason'],'capture_absent')
        d['admission']['sources'][0]['active_ids']=[8]
        with self.assertRaisesRegex(ValueError,'retained sender'):audit(d)

    def test_fault_retains_all_current_owners(self):
        d=completed();d['passed']=False;cap=d['lifecycle_capture']
        cap.update(events=cap['events'][:4],observations=4)
        d['messages'][0].update(ack_ns=None,sender_resource_released_ns=None)
        d['admission']['sources'][0].update(active_ids=[8],native_send_qps=1)
        self.assertTrue(audit(d)['reconstructed'])
        d['admission']['sources'][0]['src']=1
        with self.assertRaisesRegex(ValueError,'wrong source'):audit(d)

    def test_simultaneous_plane_acks_erase_maps_before_credit_events(self):
        d=completed();f=deepcopy(d['messages'][0]);f['id']=9;d['messages'].append(f)
        d['admission']['max_active_per_source']=2
        rows=[[0,0,8,0,0,1,2,0],[0,1,8,0,1,0,2,0],[0,2,8,0,1,0,2,1],
              [0,0,9,0,1,1,2,1],[0,1,9,0,2,0,2,1],[0,2,9,0,2,0,2,2],
              [10,3,8,0,2,0,2,2],[10,3,9,0,2,0,2,2],
              [20,4,8,0,2,0,2,2],[20,4,9,0,2,0,2,1],
              [20,5,8,0,1,0,2,0],[20,5,9,0,0,0,2,0]]
        d['lifecycle_capture'].update(events=rows,observations=len(rows))
        r=audit(d);self.assertEqual(r['peak_active']['0'],2)
        self.assertEqual(r['releases'],2)
        d['lifecycle_capture']['events'][9][7]=2
        with self.assertRaisesRegex(ValueError,'counts'):audit(d)


if __name__=='__main__':unittest.main()
