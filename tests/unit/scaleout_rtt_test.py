"""Adaptive RTO guards, independent replay and conditional termination bounds."""
from copy import deepcopy
import unittest
from scaleout_recovery_test import complete
from moe_ub_cosim.scaleout_recovery_config import validate
from moe_ub_cosim.scaleout_recovery import audit
from moe_ub_cosim.scaleout_reliability import silence_budget_ns,credit_obstructions

def adaptive():
    d=complete();r=d['recovery'];r['config']['rtt_estimator']=dict(kind='karn',min_rto_ns=5,granularity_ns=1)
    r['states'][0].update(current_rto_ns=17,rtt=dict(samples=1,discarded_probes=0,srtt_ns=5,rttvar_ns=3,
        base_rto_ns=17,ambiguity_until_bytes=0,probe_end_bytes=1000,probe_sent_ns=-1))
    r['events'][1][7]=17;r.update(observations=4,rtt_events=[[0,0,8,0,1000],[5,1,8,1000,0]],
        rtt_columns=['time_ns','event','message_id','start_or_ack','end_bytes'])
    d['messages'][0]['source_data_payload_bytes']=1000
    return d

class RttTest(unittest.TestCase):
    def test_bounded_history_replay_and_seed_corruption(self):
        d=adaptive();d['finish_ns']=11;f=d['messages'][0];f.update(dst=4,plane=0)
        d['messages'].append(dict(f,id=9,ack_ns=11,sender_resource_released_ns=11))
        r=d['recovery'];r['config']['history']=dict(max_entries_per_source=1,max_age_ns=100)
        r['states'][0].update(seed_rto_ns=10,history_hit=False)
        second=deepcopy(r['states'][0]);second.update(id=9,seed_rto_ns=17,history_hit=True);r['states'].append(second)
        r['events'] += [[6,0,0,9,0,1000,0,17,23],[11,3,0,9,1000,1000,0,17,-1]]
        r['rtt_events'] += [[6,0,9,0,1000],[11,1,9,1000,0]];r['observations']=8
        r['history']=dict(hits=1,misses=1,expirations=0,evictions=0,updates=2,entries=[dict(source=0,
            destination_ipv4=0x0b000001+4*32,rto_ns=17,evidence_ns=11,access_serial=3)])
        self.assertTrue(audit(d)['rtt']['history_reconstructed'])
        for corrupt in ('seed','hit','history','age'):
            bad=deepcopy(d)
            if corrupt=='seed':bad['recovery']['states'][1]['seed_rto_ns']=10
            elif corrupt=='hit':bad['recovery']['states'][1]['history_hit']=False
            elif corrupt=='history':bad['recovery']['history']['entries'][0]['rto_ns']=18
            else:bad['recovery']['config']['history']['max_age_ns']=1
            with self.subTest(corrupt=corrupt):
                with self.assertRaises(ValueError):audit(bad)

    def test_history_config_requires_bounds_and_adaptive(self):
        c=adaptive()['recovery']['config'];c['history']=dict(max_entries_per_source=2,max_age_ns=100);validate(c,100)
        for key,value in [('max_entries_per_source',0),('max_entries_per_source',257),('max_entries_per_source',True),('max_age_ns',0),('max_age_ns',1000000001),('extra',1)]:
            bad=deepcopy(c);bad['history'][key]=value
            with self.assertRaises(ValueError):validate(bad,100)
        c.pop('rtt_estimator')
        with self.assertRaises(ValueError):validate(c,100)

    def test_spec_replay_and_corrupt_sample(self):
        self.assertEqual(audit(adaptive())['rtt']['samples'],1)
        for key,value in [('samples',2),('srtt_ns',6),('rttvar_ns',2),('base_rto_ns',18),('probe_sent_ns',0)]:
            d=adaptive();d['recovery']['states'][0]['rtt'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):audit(d)
        d=adaptive();d['recovery']['rtt_events'][0][4]=999
        with self.assertRaises(ValueError):audit(d)
        d=adaptive();d['recovery']['events'][1][7]=18
        with self.assertRaisesRegex(ValueError,'progress'):audit(d)

    def test_karn_rejects_timeout_or_nack_ambiguity(self):
        d=adaptive();d.update(finish_ns=15);d['messages'][0].update(ack_ns=15,source_data_payload_bytes=2000)
        r=d['recovery'];r.update(observations=7,events=[[0,0,0,8,0,1000,0,10,10],
            [10,2,0,8,0,1000,1,20,30],[15,3,0,8,1000,1000,0,10,-1]],
            rtt_events=[[0,0,8,0,1000],[10,3,8,0,0],[10,0,8,0,1000],[15,1,8,1000,0]])
        s=r['states'][0];s.update(timeout_expiries=1,timeout_retransmissions=1,current_rto_ns=10,
            rtt=dict(samples=0,discarded_probes=1,srtt_ns=0,rttvar_ns=0,base_rto_ns=10,
                ambiguity_until_bytes=1000,probe_end_bytes=1000,probe_sent_ns=-1))
        self.assertEqual(audit(d)['rtt']['samples'],0)
        bad=deepcopy(d);bad['recovery']['states'][0]['rtt']['samples']=1
        with self.assertRaises(ValueError):audit(bad)

    def test_truncation_shared_budget(self):
        d=adaptive();r=d['recovery'];r.update(complete=False,observations=4,omitted_events=3,events=r['events'][:1],rtt_events=[])
        self.assertFalse(audit(d)['rtt']['reconstructed'])
        d=adaptive();d['recovery']['config']['max_events']=3
        with self.assertRaisesRegex(ValueError,'completeness'):audit(d)

    def test_closed_config_and_bounds(self):
        c=adaptive()['recovery']['config'];validate(c,100)
        for key,value in [('kind','rfc6298'),('min_rto_ns',0),('min_rto_ns',11),('min_rto_ns',True),
                          ('granularity_ns',0),('granularity_ns',21),('extra',1)]:
            bad=deepcopy(c);bad['rtt_estimator'][key]=value
            with self.subTest(key=key):
                with self.assertRaises(ValueError):validate(bad,100)
        with self.assertRaises(ValueError):validate(dict(mode='native',rtt_estimator=c['rtt_estimator']),100)

    def test_no_ack_progress_bound(self):
        self.assertEqual(silence_budget_ns(40000,160000,2),280000)
        self.assertEqual(silence_budget_ns(40000,160000,3),440000)
        self.assertEqual(silence_budget_ns(37566,160000,3),422962)
        for args in [(0,10,1),(11,10,1),(1,10,0),(True,10,1),(1,10,17)]:
            with self.assertRaises(ValueError):silence_budget_ns(*args)

    def test_credit_obstruction_is_snapshot_not_network_delay(self):
        def flow(mid,b,ready,peer):return dict(id=mid,src=0,dst=peer,bytes=b,receive_ready_ns=ready,ack_callbacks=0)
        result=dict(messages=[flow(1,6144,10,4),flow(2,4096,None,6),flow(3,1000,None,2)],
            admission=dict(policy='fifo',max_active_per_source=3,max_active_bytes_per_source=8192,
                sources=[dict(src=0,active_ids=[1],pending_ids=[2,3],decision_pending=False)]))
        s=credit_obstructions(result)['sources'][0]
        self.assertEqual(s['reason'],'fifo_head_bytes_do_not_fit');self.assertEqual(s['remaining_bytes'],2048)
        self.assertEqual(s['data_ready_but_unconfirmed_owner_ids'],[1]);self.assertFalse(s['policy_eligible_now'])
        result['admission']['policy']='destination_round_robin'
        s=credit_obstructions(result)['sources'][0]
        self.assertTrue(s['policy_eligible_now']);self.assertEqual(s['byte_eligible_head_ids'],[3])
        result['admission']['max_active_per_source']=1
        self.assertEqual(credit_obstructions(result)['sources'][0]['reason'],'source_message_slots_full')
        result['messages'][0]['src']=2
        with self.assertRaises(ValueError):credit_obstructions(result)

if __name__=='__main__':unittest.main()
