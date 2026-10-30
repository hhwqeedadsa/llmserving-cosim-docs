"""Delayed complete copies: actual scheduler/KV plus prefix-only policy checks."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from whole_call_test import config as eager_config,event
from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import Frontend,make_wave
from moe_ub_cosim.scaleout_verify import verify_replay_requests
from moe_ub_cosim.whole_call import configuration,consumer,logical_finish
from moe_ub_cosim.delayed_whole import audit_standby
from moe_ub_cosim.service_wakeup import audit as audit_timer


def config():
    c=eager_config();c['whole_call_replication'].update(schema_version='moe-whole-call-replication/2',
        policy='delayed_complete_segment/1',hedge_after_ns=70,max_wakeup_events=4)
    return c


def decide(now=0,index=0,completed=(),wake=None):
    return dict(type='DECIDE',now_ns=now,decision_id=index,completed=list(completed),
                available_rank_ids=list(range(4)),wakeup_ns=wake)


def fresh(c=None):
    c=c or config();r=Replay(c,ROOT);return c,r,consumer(c,r)


def submit(c,r,w,groups,now):
    for i in groups:w.register(make_wave(c,c['parallel']['groups'][i],i,0,1,r),now)


class DelayedChecks(unittest.TestCase):
    def test_normal_primary_result_skips_unstarted_replica_without_execution(self):
        c,r,w=fresh();groups,wake=w.choose(decide());self.assertEqual((groups,wake),([0],70))
        submit(c,r,w,groups,0);w.accept(event('g0.w0',60,100))
        self.assertEqual(w.choose(decide(60,1)),([],None))
        report=w.snapshot();self.assertTrue(report['all_attempts_retired'])
        self.assertEqual((report['execution_attempt_requests'],report['reserved_attempt_requests']),(2,4))
        self.assertEqual(logical_finish(report,{'g0.w0':(0,60)}),60)
        self.assertIsNone(report['calls']['c0']['attempts']['replica']['finished_ns'])
        with self.assertRaises(ValueError):w.accept(event('g1.w0',65,101))

    def test_deadline_requires_native_wake_and_started_loser_retires(self):
        for winner in ('g0.w0','g1.w0'):
            c,r,w=fresh();submit(c,r,w,w.choose(decide())[0],0)
            groups,wake=w.choose(decide(70,1,wake=70));self.assertEqual((groups,wake),([1],None))
            submit(c,r,w,groups,70)
            w.accept(event(winner,90,100));self.assertFalse(w.snapshot()['all_attempts_retired'])
            loser='g1.w0' if winner=='g0.w0' else 'g0.w0'
            w.accept(event(loser,100,101));self.assertEqual(w.choose(decide(100,2)),([],None))
            history={'g0.w0':(0,90 if winner=='g0.w0' else 100),'g1.w0':(70,90 if winner=='g1.w0' else 100)}
            self.assertEqual(logical_finish(w.snapshot(),history),90)

    def test_same_time_result_known_before_decision_skips_but_later_result_cannot(self):
        c,r,w=fresh();submit(c,r,w,w.choose(decide())[0],0);w.accept(event('g0.w0',70,1))
        self.assertEqual(w.choose(decide(70,1,wake=70)),([],None))
        c,r,w=fresh();submit(c,r,w,w.choose(decide())[0],0)
        groups,_=w.choose(decide(70,1,wake=70));submit(c,r,w,groups,70)
        w.accept(event('g0.w0',70,1));self.assertIsNone(w.snapshot()['calls']['c0']['attempts']['replica']['skipped_ns'])

    def test_missing_late_early_or_foreign_deadline_cannot_launch(self):
        for now,wake in ((70,None),(71,70),(70,69)):
            c,r,w=fresh();submit(c,r,w,w.choose(decide())[0],0)
            with self.subTest(now=now,wake=wake),self.assertRaises(ValueError):w.choose(decide(now,1,wake=wake))
        c,r,w=fresh();submit(c,r,w,[0],0)
        with self.assertRaises(ValueError):submit(c,r,w,[1],69)
        w.accept(event('g0.w0',71,1))
        with self.assertRaises(ValueError):w.choose(decide(71,1))

    def test_multicall_policy_order_matches_group_order_not_declaration_order(self):
        c=config();c['parallel']['groups']=[[0,2],[1,3],[4,6],[5,7]]
        c['route_replay']['requests'][1]['rank_id']=4
        c['whole_call_replication'].update(calls=[dict(id='c1',primary_group=2,replica_group=3),
            dict(id='c0',primary_group=0,replica_group=1)],max_events=14)
        c,r,w=fresh(c);self.assertEqual(w.choose(decide())[0],[0,2])
        submit(c,r,w,[0,2],0);w.accept(event('g0.w0',60,1))
        self.assertEqual(w.choose(decide(60,1)),([],70))
        self.assertEqual(w.choose(decide(70,2,wake=70)),([3],None))

    def test_strict_delayed_schema_deadline_and_incompatible_observers(self):
        for key,value in [('hedge_after_ns',True),('hedge_after_ns',0),('hedge_after_ns',5000000),
                          ('max_wakeup_events',1),('policy','eager_complete_segment/1')]:
            c=config();c['whole_call_replication'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):configuration(c)
        for key in ('source_ack_watchdog','contribution_tracking','inflight_helpers'):
            c=config();c[key]={}
            with self.subTest(key=key),self.assertRaises(ValueError):configuration(c)

    def test_skipped_replica_cannot_gain_submission_or_extra_graph(self):
        c,r,w=fresh();submit(c,r,w,[0],0);w.accept(event('g0.w0',60,1));w.choose(decide(60,1))
        for kind in ('submitted','late_skip','extra_graph','missing_primary'):
            report=w.snapshot();history={'g0.w0':(0,60)}
            if kind=='submitted':report['calls']['c0']['attempts']['replica']['submitted_ns']=70
            elif kind=='late_skip':report['calls']['c0']['attempts']['replica']['skipped_ns']=71
            elif kind=='extra_graph':history['g1.w0']=(70,90)
            else:history={}
            with self.subTest(kind=kind),self.assertRaises(ValueError):logical_finish(report,history)

    def test_native_argument_is_explicit_and_default_eager_unchanged(self):
        from moe_ub_cosim.service_transport import native_arguments
        with tempfile.TemporaryDirectory() as path:
            c=config();c.pop('transport');c['transport']={}
            # Empty transport is sufficient for argument plumbing; no network executes.
            with patch('moe_ub_cosim.scaleout_rdma.save'):
                args=native_arguments(c,Path(path))
                self.assertIn('--control-wakeup',args)
                c['whole_call_replication']=eager_config()['whole_call_replication']
                self.assertNotIn('--control-wakeup',native_arguments(c,Path(path)))

    def run_frontend(self,launch):
        c,r,w=fresh()
        class Preflight:
            def accept_wave(self,wave,now):pass
        f=Frontend(c,4,1,r,message_preflight=Preflight());f.whole=w;transcript=[]
        held_ids={q.id for requests in f.held_groups[1].values() for q in requests}
        self.assertEqual(held_ids,{1,3});self.assertGreater(f.standby_initial_bytes,0)
        self.assertTrue(all(q.status.name=='WAITING' for q in f.requests if q.id in held_ids))
        self.assertTrue(all(f.schedulers[rank].is_request_empty() for rank in (1,3)))
        def step(e):
            reply=f.decide(e,Path(tempfile.gettempdir()),None)
            for path in reply['submissions']:
                i=int(Path(path).name.split('.')[0][1:]);submit(c,r,w,[i],e['now_ns'])
            transcript.append(dict(event=e,reply=reply));return reply
        with patch('moe_ub_cosim.scaleout_service.export_wave'):
            step(decide())
            if launch:
                step(decide(70,1,wake=70));w.accept(event('g1.w0',90,100))
                step(decide(90,2,[dict(graph_id='g1.w0',finished_ns=90)]))
                w.accept(event('g0.w0',100,101));last=step(decide(100,3,[dict(graph_id='g0.w0',finished_ns=100)]))
            else:
                w.accept(event('g0.w0',60,100));last=step(decide(60,1,[dict(graph_id='g0.w0',finished_ns=60)]))
        self.assertTrue(last['service_finished']);self.assertFalse(f.held_groups)
        self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in f.schedulers.values()))
        requests=[]
        for q in f.requests:
            skipped=q.id in f.skipped_requests
            row=dict(id=q.id,state='NOT_STARTED' if skipped else 'OBSERVATION_ENDED',scheduler_state=q.status.name,
                computed_tokens=q.num_computed_tokens,reached_tokens=q.num_tokens_reached,intertoken_ns=q.itl,
                initial_context_tokens=r.records[q.id]['context_tokens'])
            if skipped:row['skipped_before_execution_ns']=f.skipped_requests[q.id]
            requests.append(row)
        report=dict(requests=requests,history=f.history,transcript=transcript,scheduler_calls=f.calls,
            active_source_ranks=sorted(f.schedulers),whole_call_replication=w.snapshot(),
            standby_memory=dict(initial_reserved_bytes=f.standby_initial_bytes,releases=f.standby_releases,final_used_bytes=0))
        verify_replay_requests(c,r,report);audit_standby(c,r,report)
        return c,r,report

    def test_real_scheduler_skip_frees_kv_without_fake_ITL_or_finish(self):
        c,r,report=self.run_frontend(False)
        for kind in ('fake_ITL','fake_finish','fake_tokens','fake_KV','fake_release'):
            bad=deepcopy(report)
            if kind=='fake_ITL':bad['requests'][1]['intertoken_ns']=[60]
            elif kind=='fake_finish':bad['requests'][1]['scheduler_state']='FINISHED'
            elif kind=='fake_tokens':bad['requests'][1]['computed_tokens']+=1
            elif kind=='fake_KV':bad['standby_memory']['initial_reserved_bytes']-=16
            else:bad['standby_memory']['releases'][0]['time_ns']-=1
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                verify_replay_requests(c,r,bad);audit_standby(c,r,bad)

    def test_real_scheduler_launches_at_deadline_and_frees_only_after_actual_completion(self):
        c,r,report=self.run_frontend(True)
        self.assertEqual(report['history'][1]['start_ns'],70)
        self.assertEqual(report['standby_memory']['releases'][0]['kind'],'executed_release')
        self.assertEqual(report['standby_memory']['releases'][0]['time_ns'],90)

    def test_native_timer_report_reconstructed_and_tampering_rejected(self):
        c=config();transcript=[dict(event=decide(),reply=dict(wake_at_ns=70)),
            dict(event=decide(70,1,wake=70),reply=dict(wake_at_ns=None))]
        events=[dict(sequence=0,time_ns=0,kind='armed',deadline_ns=70,ticket=0),
                dict(sequence=1,time_ns=70,kind='fired',deadline_ns=70,ticket=0)]
        result=dict(service=dict(control_wakeup=dict(schema_version='moe-service-wakeup-report/1',
            max_events=4,events=events,pending_deadline_ns=None)))
        audit_timer(c,result,dict(transcript=transcript))
        for key in ('time_ns','deadline_ns','ticket'):
            bad=deepcopy(result);bad['service']['control_wakeup']['events'][1][key]+=1
            with self.subTest(key=key),self.assertRaises(ValueError):audit_timer(c,bad,dict(transcript=transcript))
        bad=deepcopy(transcript);bad[-1]['event']['wakeup_ns']=None
        with self.assertRaises(ValueError):audit_timer(c,result,dict(transcript=bad))


if __name__=='__main__':unittest.main()
