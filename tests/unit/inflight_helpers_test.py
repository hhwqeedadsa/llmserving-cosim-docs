"""Functional artificial callback checks; no network or GPU execution."""
from copy import deepcopy
import json
import unittest

from contribution_binding_test import config as base_config, wave as base_wave, events, Routes
from moe_ub_cosim.contribution_binding import ContributionBinding
from moe_ub_cosim.execution_events import ExecutionObserver
from moe_ub_cosim.inflight_helpers import HelperObserver, candidates, configuration, audit_compute, audit_transport
from moe_ub_cosim.native_control import event


def config(policy='first_missing_bundle/1'):
    c=base_config();c['parallel']['groups'].append([2,3])
    c['model'].update(hidden_size=64,activation_bytes=2,top_k=3)
    c['compute']=dict(expert_ns_per_contribution=2)
    c['token_transport']['local_reduce_ns_per_extra']=1
    c['source_ack_watchdog']=dict(schema_version='moe-source-ack-watchdog/1',timeout_ns=1,max_messages=8,max_notifications=8)
    c['inflight_helpers']=dict(schema_version='moe-inflight-helpers/1',policy=policy,
        max_attempts=1,max_events=12,max_compute_tasks=64,max_decisions=8)
    return c


def wave(c):
    w=base_wave()
    for m in w['messages']:m['plane']=0
    w['helper_candidates']=candidates(c,w,Routes())
    return w


class HelperChecks(unittest.TestCase):
    def runtime(self,c=None):
        c=c or config();binding=ContributionBinding(c,Routes(),ExecutionObserver(c['execution_events']))
        w=wave(c);binding.register(w);helper=HelperObserver(c,binding);helper.register(w)
        for row in events()[:10]:binding.accept(row);helper.source_execution(row)
        notice=dict(sequence=0,time_ns=3,message=7)
        helper.source_notice(notice)
        decision=dict(type='HELPER_DECIDE',decision_id=0,now_ns=3,notice_sequence=0,message=7,observed_events=binding.observer.count)
        return binding,helper,decision

    def test_declared_replica_covers_complete_bundle_and_exact_costs(self):
        rows=wave(config())['helper_candidates'];self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual((row['source'],row['owner'],row['provider']),(0,1,3))
            self.assertEqual(row['experts'],[2]);self.assertEqual(row['compute_ns'],2)
            self.assertEqual(row['reduce_ns'],0)
            self.assertGreaterEqual(row['input_message'],2**40)
            self.assertEqual(row['result_message'],row['input_message']+1)
        self.assertNotEqual(rows[0]['input_message'],rows[1]['input_message'])

    def test_first_helper_real_events_win_once_but_original_resources_remain(self):
        binding,helper,decision=self.runtime();reply=helper.decide(decision);id=reply['attempts'][0]
        steps=[('accepted',3),('input_post',3),('input_ready',4),('compute_begin',4),('input_ack',6),
               ('compute_done',6),('reduce_begin',6),('reduce_done',6),('result_post',6),
               ('result_ready',7),('result_ack',8),('retired',8)]
        merged=[(row['time_ns'],0,i,'original',row) for i,row in enumerate(events()[10:])]
        merged += [(now,1,i,'helper',kind) for i,(kind,now) in enumerate(steps)]
        at11=None
        for now,_,_,source,row in sorted(merged):
            if source=='original':binding.accept(row);helper.source_execution(row)
            else:helper.accept(dict(type='HELPER_OBSERVE',schema_version='moe-helper-event/1',sequence=helper.events,
                observed_events=binding.observer.count,time_ns=now,id=id,kind=row))
            if now==11:at11=binding.calls['g0.w0.l0.r0.s0']['runtime'].snapshot()
        final=binding.calls['g0.w0.l0.r0.s0']['runtime'].snapshot()
        self.assertEqual(final['winners']['r1'],dict(attempt='helper.r1',time_ns=7))
        self.assertEqual(final['logical_complete_ns'],11)
        self.assertFalse(at11['declared_call_retirable'])
        self.assertIsNone(at11['resources']['graph.g0.w0.r1']['closed_ns'])
        self.assertTrue(final['declared_call_retirable'])
        self.assertEqual(final['attempts']['original.r1']['retired_ns'],15)
        self.assertEqual(final['attempts']['helper.r1']['retired_ns'],8)
        late=[row for row in final['events'] if row['kind']=='result' and row['attempt']=='original.r1']
        self.assertEqual(late[0]['outcome'],'redundant')
        self.assertEqual(binding.summary()['lifecycle_events'],52)
        self.assertEqual(binding.summary()['reserved_lifecycle_events'],52)

    def test_observe_only_reply_still_synchronizes_but_submits_nothing(self):
        binding,helper,decision=self.runtime(config('observe_only/1'))
        self.assertEqual(helper.decide(decision)['attempts'],[])
        self.assertEqual(helper.decisions,1);self.assertFalse(helper.selected)
        for row in events()[10:]:binding.accept(row);helper.source_execution(row)
        self.assertEqual(binding.summary()['lifecycle_events'],40)

    def test_policy_reads_source_result_and_input_producer_not_remote_truth(self):
        binding,helper,decision=self.runtime()
        binding.observer.messages[7]['recv_complete']=3  # Not used as a recovery decision input.
        self.assertEqual(len(helper.decide(decision)['attempts']),1)
        binding,helper,decision=self.runtime()
        for row in events()[10:26]:binding.accept(row);helper.source_execution(row)
        helper.source_notice(dict(sequence=1,time_ns=14,message=7))
        decision.update(now_ns=14,notice_sequence=1,observed_events=binding.observer.count)
        self.assertFalse(helper.decide(decision)['attempts'])

    def test_sync_decision_identity_time_fields_and_replay_refused(self):
        for field,value in [('now_ns',4),('decision_id',True),('notice_sequence',1),('observed_events',0),('fault_end_ns',3)]:
            _,helper,decision=self.runtime()
            with self.subTest(field=field),self.assertRaises(ValueError):helper.decide(dict(decision,**{field:value}))
            self.assertEqual(helper.decisions,0)
        _,helper,decision=self.runtime();helper.decide(decision)
        with self.assertRaises(ValueError):helper.decide(decision)

    def test_no_early_compute_result_or_duplicate_events(self):
        for kind in ('input_ready','compute_begin','result_ready','retired'):
            binding,helper,decision=self.runtime();id=helper.decide(decision)['attempts'][0]
            row=dict(type='HELPER_OBSERVE',schema_version='moe-helper-event/1',sequence=0,
                     observed_events=binding.observer.count,time_ns=3,id=id,kind=kind)
            with self.subTest(kind=kind),self.assertRaises(ValueError):helper.accept(row)
            self.assertFalse(helper.states)
        binding,helper,decision=self.runtime();id=helper.decide(decision)['attempts'][0]
        row=dict(type='HELPER_OBSERVE',schema_version='moe-helper-event/1',sequence=0,
                 observed_events=binding.observer.count,time_ns=3,id=id,kind='accepted')
        helper.accept(row)
        with self.assertRaises(ValueError):helper.accept(dict(row,sequence=1))

    def test_tampered_candidate_cover_provider_and_cost_rejected(self):
        for field,value in [('experts',[3]),('provider',2),('compute_ns',0),('input_bytes',1)]:
            c=config();b=ContributionBinding(c,Routes(),ExecutionObserver(c['execution_events']));w=wave(c)
            b.register(w);h=HelperObserver(c,b);w['helper_candidates'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):h.register(w)
            self.assertFalse(h.catalog)

    def test_strict_configuration_and_channel_is_opt_in(self):
        self.assertIsNone(configuration({}))
        for name,value in [('max_attempts',0),('max_compute_tasks',True),('max_events',11),('policy','oracle')]:
            c=config();c['inflight_helpers'][name]=value
            with self.subTest(name=name),self.assertRaises(ValueError):configuration(c)
        c=config();c['parallel']['groups'].pop()
        with self.assertRaises(ValueError):configuration(c)
        _,helper,decision=self.runtime();line='MOE_RDMA_EVENT '+json.dumps(decision)+'\n'
        with self.assertRaises(ValueError):event(line,observations=True,source_ack_timeouts=True)
        self.assertEqual(event(line,observations=True,source_ack_timeouts=True,helpers=True),decision)

    def test_helper_callbacks_must_match_actual_transport_acceptance_and_ack(self):
        row=wave(config())['helper_candidates'][0]
        state=dict(accepted=3,input_post=3,input_ready=5,input_ack=7,result_post=8,result_ready=10,result_ack=12,retired=12)
        messages={}
        for result in (False,True):
            prefix='result' if result else 'input';mid=row[prefix+'_message']
            messages[mid]=dict(id=mid,src=row['provider' if result else 'source'],dst=row['source' if result else 'provider'],
                bytes=row[prefix+'_bytes'],plane=row['plane'],phase='helper_'+prefix,helper_id=row['id'],
                ready_ns=state[prefix+'_post'],published_ns=state[prefix+'_post'],receive_ready_ns=state[prefix+'_ready'],ack_ns=state[prefix+'_ack'])
        audit_transport(row,state,messages)
        for prefix in ('input','result'):
            for field in ('ready_ns','receive_ready_ns','ack_ns','bytes','src'):
                bad=deepcopy(messages);bad[row[prefix+'_message']][field]+=1
                with self.subTest(prefix=prefix,field=field),self.assertRaises(ValueError):audit_transport(row,state,bad)


if __name__=='__main__':unittest.main()
