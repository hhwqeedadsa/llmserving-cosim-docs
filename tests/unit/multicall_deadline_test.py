"""Independent complete-call deadlines use one earliest native wakeup."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from delayed_whole_test import decide,fresh,submit
from whole_call_test import config as eager,event
from moe_ub_cosim.whole_call import configuration,deadlines,logical_finish
from moe_ub_cosim.service_wakeup import audit as audit_timer
from moe_ub_cosim.scaleout_service import Frontend
from moe_ub_cosim.delayed_whole import audit_standby


def config():
    c=eager();c['parallel']['groups']=[[0,2],[1,3],[4,6],[5,7]]
    c['route_replay']['requests'][1]['rank_id']=4
    c['whole_call_replication']=dict(schema_version='moe-whole-call-replication/3',
        policy='delayed_complete_segment/1',initial_state='declared_equivalent_ready_kv/1',
        max_events=14,max_wakeup_events=8,calls=[dict(id='healthy',primary_group=2,replica_group=3,hedge_after_ns=120),
            dict(id='target',primary_group=0,replica_group=1,hedge_after_ns=70)])
    return c


class PerCallChecks(unittest.TestCase):
    def test_earliest_deadline_is_independent_of_declaration_order(self):
        c,r,w=fresh(config());self.assertEqual(w.choose(decide()),([0,2],70))
        self.assertEqual(deadlines(configuration(c)),{'healthy':120,'target':70})
        submit(c,r,w,[0,2],0);w.accept(event('g0.w0',60,1))
        self.assertEqual(w.choose(decide(60,1)),([],120))
        w.accept(event('g2.w0',100,2));self.assertEqual(w.choose(decide(100,2)),([],None))
        self.assertEqual(logical_finish(w.snapshot(),{'g0.w0':(0,60),'g2.w0':(0,100)}),100)

    def test_only_due_call_starts_other_call_remains_held_and_can_skip(self):
        c,r,w=fresh(config());submit(c,r,w,[0,2],0)
        self.assertEqual(w.choose(decide(70,1,wake=70)),([1],120));submit(c,r,w,[1],70)
        w.accept(event('g2.w0',80,1));self.assertEqual(w.choose(decide(80,2)),([],None))
        w.accept(event('g1.w0',100,2));w.accept(event('g0.w0',110,3))
        self.assertEqual(logical_finish(w.snapshot(),{'g0.w0':(0,110),'g1.w0':(70,100),'g2.w0':(0,80)}),100)
        self.assertIsNone(w.snapshot()['calls']['healthy']['attempts']['replica']['submitted_ns'])

    def test_both_actual_deadlines_fire_in_order(self):
        c,r,w=fresh(config());submit(c,r,w,[0,2],0)
        submit(c,r,w,w.choose(decide(70,1,wake=70))[0],70)
        self.assertEqual(w.choose(decide(120,2,wake=120)),([3],None));submit(c,r,w,[3],120)
        for seq,(graph,t) in enumerate((('g1.w0',130),('g3.w0',140),('g0.w0',150),('g2.w0',160))):w.accept(event(graph,t,seq))
        self.assertEqual(logical_finish(w.snapshot(),{'g0.w0':(0,150),'g1.w0':(70,130),'g2.w0':(0,160),'g3.w0':(120,140)}),140)

    def test_equal_deadlines_start_due_calls_in_group_order(self):
        c=config();c['whole_call_replication']['calls'][0]['hedge_after_ns']=70
        c,r,w=fresh(c);submit(c,r,w,[0,2],0)
        self.assertEqual(w.choose(decide(70,1,wake=70)),([1,3],None))

    def test_no_call_may_borrow_another_call_deadline(self):
        c,r,w=fresh(config());submit(c,r,w,[0,2],0)
        for now in (69,90,121):
            with self.subTest(now=now),self.assertRaises(ValueError):w.choose(decide(now,1,wake=now))
        with self.assertRaises(ValueError):submit(c,r,w,[3],70)

    def test_per_call_schema_rejects_ambiguous_and_missing_deadlines(self):
        for change in ('missing','bool','zero','large','global','old_schema_extra'):
            c=config();o=c['whole_call_replication']
            if change=='missing':o['calls'][0].pop('hedge_after_ns')
            elif change=='bool':o['calls'][0]['hedge_after_ns']=True
            elif change=='zero':o['calls'][0]['hedge_after_ns']=0
            elif change=='large':o['calls'][0]['hedge_after_ns']=5000000
            elif change=='global':o['hedge_after_ns']=70
            else:o.update(schema_version='moe-whole-call-replication/2',hedge_after_ns=70)
            with self.subTest(change=change),self.assertRaises(ValueError):configuration(c)

    def test_single_native_clock_cancel_and_replace_is_independently_audited(self):
        c=config();events=[dict(sequence=0,time_ns=0,kind='armed',deadline_ns=70,ticket=0),
            dict(sequence=1,time_ns=60,kind='cancelled',deadline_ns=70,ticket=0),
            dict(sequence=2,time_ns=60,kind='armed',deadline_ns=120,ticket=1),
            dict(sequence=3,time_ns=100,kind='cancelled',deadline_ns=120,ticket=1)]
        result=dict(service=dict(control_wakeup=dict(schema_version='moe-service-wakeup-report/1',
            max_events=8,events=events,pending_deadline_ns=None)))
        transcript=[dict(event=decide(),reply=dict(wake_at_ns=70)),dict(event=decide(60,1),reply=dict(wake_at_ns=120)),
                    dict(event=decide(100,2),reply=dict(wake_at_ns=None))]
        audit_timer(c,result,dict(transcript=transcript))
        bad=deepcopy(result);bad['service']['control_wakeup']['events'][2]['deadline_ns']=70
        with self.assertRaises(ValueError):audit_timer(c,bad,dict(transcript=transcript))

    def test_real_schedulers_hold_two_independent_copies_and_release_each_on_its_own_result(self):
        c,r,w=fresh(config())
        class Preflight:
            def accept_wave(self,wave,now):pass
        f=Frontend(c,8,1,r,message_preflight=Preflight());f.whole=w;transcript=[]
        self.assertEqual(set(f.held_groups),{1,3})
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave'):
            def step(e):
                e['available_rank_ids']=list(range(8));reply=f.decide(e,Path(folder),None)
                for path in reply['submissions']:
                    index=int(Path(path).name[1:].split('.')[0]);submit(c,r,w,[index],e['now_ns'])
                transcript.append(dict(event=e,reply=reply));return reply
            self.assertEqual(step(decide())['wake_at_ns'],70)
            w.accept(event('g0.w0',60,1))
            reply=step(decide(60,1,[dict(graph_id='g0.w0',finished_ns=60)]))
            self.assertEqual(reply['wake_at_ns'],120);self.assertEqual(set(f.held_groups),{3})
            self.assertFalse(reply['service_finished'])
            w.accept(event('g2.w0',100,2))
            self.assertTrue(step(decide(100,2,[dict(graph_id='g2.w0',finished_ns=100)]))['service_finished'])
        self.assertEqual(f.calls,2);self.assertFalse(f.held_groups)
        self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in f.schedulers.values()))
        self.assertTrue(all(q.itl==[] and q.status.name=='WAITING' for q in f.requests if q.id in f.skipped_requests))
        audit_standby(c,r,dict(transcript=transcript,whole_call_replication=w.snapshot(),standby_memory=dict(
            initial_reserved_bytes=f.standby_initial_bytes,releases=f.standby_releases,final_used_bytes=0)))

if __name__=='__main__':unittest.main()
