"""Actual source prefixes gate complete copies; no future receiver truth."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sequential_whole_test import config as sequential,submit
from delayed_whole_test import fresh,decide
from whole_call_test import event
from moe_ub_cosim.whole_call import configuration,logical_finish
from moe_ub_cosim.scaleout_service import Frontend,make_wave
from moe_ub_cosim.delayed_whole import audit_standby


def config():
    c=sequential();o=c['whole_call_replication'];o.update(schema_version='moe-whole-call-replication/5',max_guard_events=64,max_guard_messages=256,max_wakeup_events=64)
    for spec in o['calls']:spec['source_ack_timeout_ns']=20 if spec['id']=='target' else None
    return c

def callback(kind,now,seq,message,graph='g0.w0'):
    return dict(schema_version='moe-execution-event/1',type='OBSERVE',kind=kind,time_ns=now,sequence=seq,
        graph_id=graph,message=message['id'],rank=message['src'])

def start():
    c,r,w=fresh(config());groups,wake=w.choose(decide());assert (groups,wake)==([0,2],20)
    submit(c,r,w,0,0,0);submit(c,r,w,2,0,0)
    m=make_wave(c,c['parallel']['groups'][0],0,0,1,r)['messages'][0]
    return c,r,w,m


class GuardedChecks(unittest.TestCase):
    def test_normal_final_result_skips_held_copy_and_cancels_its_checks(self):
        c,r,w,m=start();w.accept(event('g0.w0',10,0));submit(c,r,w,0,1,10)
        w.accept(event('g0.w1',15,1));self.assertEqual(w.choose(decide(15,1)),([],120))
        self.assertEqual(w.calls['target']['attempts']['replica']['skipped_ns'],15)
        self.assertEqual(w.calls['target']['attempts']['replica']['waves'],[])

    def test_guard_changes_policy_without_changing_two_step_input_identity(self):
        _,_,guard=fresh(config());_,_,plain=fresh(sequential())
        self.assertEqual(guard.calls['target']['input_identity'],plain.calls['target']['input_identity'])

    def test_timeout_uses_actual_post_not_task_zero(self):
        c,r,w,m=start();w.accept(callback('message_send_post',5,0,m))
        self.assertEqual(w.choose(decide(20,1,wake=20)),([],25))
        self.assertEqual(w.choose(decide(25,2,wake=25)),([1],120))
        submit(c,r,w,1,0,25)
        self.assertEqual(w.snapshot()['calls']['target']['attempts']['replica']['submitted_ns'],25)

    def test_confirmation_before_poll_prevents_launch_and_keeps_later_protection(self):
        c,r,w,m=start();w.accept(callback('message_send_post',5,0,m));w.choose(decide(20,1,wake=20))
        w.accept(callback('message_send_complete',24,1,m))
        self.assertEqual(w.choose(decide(25,2,wake=25)),([],45))
        self.assertIsNone(w.snapshot()['calls']['target']['attempts']['replica']['skipped_ns'])

    def test_no_pending_work_does_not_permanently_disable_guard(self):
        c,r,w,m=start();self.assertEqual(w.choose(decide(20,1,wake=20)),([],40))
        w.accept(callback('message_send_post',30,0,m))
        self.assertEqual(w.choose(decide(40,2,wake=40)),([],50))
        self.assertEqual(w.choose(decide(50,3,wake=50)),([1],120))

    def test_same_time_confirmation_depends_on_observed_prefix(self):
        for before in (True,False):
            c,r,w,m=start();w.accept(callback('message_send_post',5,0,m))
            if before:w.accept(callback('message_send_complete',25,1,m))
            groups,_=w.choose(decide(25,1,wake=25));self.assertEqual(1 in groups,not before)
            if not before:w.accept(callback('message_send_complete',25,1,m))

    def test_full_call_fallback_survives_absent_posts(self):
        c,r,w,m=start()
        for now in (20,40,60):w.choose(decide(now,now,wake=now))
        self.assertEqual(w.choose(decide(70,4,wake=70)),([1],120))
        self.assertEqual(w.snapshot()['source_ack_guard']['decisions'][-1]['reason'],'fallback')

    def test_replica_cannot_start_without_a_real_authorizing_decision(self):
        c,r,w,m=start()
        with self.assertRaises(ValueError):submit(c,r,w,1,0,25)

    def test_timeout_starts_once_and_healthy_call_does_not_borrow_it(self):
        c,r,w,m=start();w.accept(callback('message_send_post',5,0,m))
        groups,_=w.choose(decide(25,1,wake=25));submit(c,r,w,1,0,25)
        self.assertEqual(w.choose(decide(26,2)),([],120))
        self.assertIsNone(w.calls['healthy']['attempts']['replica']['submitted_ns'])

    def test_binding_completion_order_and_foreign_messages_are_rejected(self):
        for kind in ('foreign','wrong_rank','complete_without_post','duplicate_post','backwards'):
            c,r,w,m=start()
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                e=callback('message_send_post',5,0,m)
                if kind=='foreign':e['message']=999999
                elif kind=='wrong_rank':e['rank']=511
                elif kind=='complete_without_post':e['kind']='message_send_complete'
                elif kind=='duplicate_post':w.accept(e);e=callback('message_send_post',6,1,m)
                else:w.accept(e);e=callback('message_send_complete',4,1,m)
                w.accept(e)

    def test_guard_decision_budget_rejects_before_state_change(self):
        c,r,w,m=start();w.option['max_guard_events']=len(w.guard_decisions)
        old=deepcopy(w.snapshot())
        with self.assertRaises(ValueError):w.choose(decide(20,1,wake=20))
        self.assertEqual(old,w.snapshot())

    def test_message_budget_rejects_before_graph_registration(self):
        c,r,w=fresh(config());w.choose(decide());w.option['max_guard_messages']=1
        old=deepcopy(w.snapshot())
        with self.assertRaises(ValueError):submit(c,r,w,0,0,0)
        self.assertEqual(old,w.snapshot())

    def test_missing_or_ambiguous_guard_options_reject(self):
        for kind in ('missing','bool','zero','all_off','old_extra','budget'):
            c=config();o=c['whole_call_replication']
            target=next(s for s in o['calls'] if s['id']=='target')
            if kind=='missing':target.pop('source_ack_timeout_ns')
            elif kind=='bool':target['source_ack_timeout_ns']=True
            elif kind=='zero':target['source_ack_timeout_ns']=0
            elif kind=='all_off':target['source_ack_timeout_ns']=None
            elif kind=='old_extra':o['schema_version']='moe-whole-call-replication/4'
            else:o['max_guard_events']=0
            with self.subTest(kind=kind),self.assertRaises(ValueError):configuration(c)

    def test_real_scheduler_keeps_both_steps_and_releases_KV_only_at_actual_end(self):
        c,r,w=fresh(config())
        class Preflight:
            def accept_wave(self,wave,now):pass
        f=Frontend(c,8,1,r,message_preflight=Preflight());f.whole=w;transcript=[]
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave'):
            def step(e):
                e['available_rank_ids']=list(range(8));reply=f.decide(e,Path(folder),None)
                for p in reply['submissions']:
                    group,number=Path(p).name.split('.');submit(c,r,w,int(group[1:]),int(number[1:]),e['now_ns'])
                transcript.append(dict(event=e,reply=reply));return reply
            def retire(graph,now,seq):
                w.accept(event(graph,now,seq));return step(decide(now,len(transcript),[dict(graph_id=graph,finished_ns=now)]))
            step(decide());m=make_wave(c,c['parallel']['groups'][0],0,0,1,r)['messages'][0]
            w.accept(callback('message_send_post',5,0,m));step(decide(20,1,wake=20));step(decide(25,2,wake=25))
            retire('g1.w0',40,1);self.assertFalse(f.standby_releases)
            w.accept(callback('message_send_complete',45,2,m))
            retire('g0.w0',50,3);retire('g2.w0',55,4)
            retire('g1.w1',60,5);self.assertEqual(w.calls['target']['winner'],'replica')
            retire('g2.w1',65,6);last=retire('g0.w1',80,7)
            self.assertTrue(last['service_finished'])
            self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in f.schedulers.values()))
            report=w.snapshot();self.assertEqual(logical_finish(report,{h['graph_id']:(h['start_ns'],h['finished_ns']) for h in f.history}),65)
            audit_standby(c,r,dict(transcript=transcript,whole_call_replication=report,standby_memory=dict(
                initial_reserved_bytes=f.standby_initial_bytes,releases=f.standby_releases,final_used_bytes=0)))

if __name__=='__main__':unittest.main()
