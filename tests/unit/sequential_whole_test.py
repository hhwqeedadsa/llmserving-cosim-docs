"""Final-step result boundaries, real scheduler KV and ordered Decode graphs."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multicall_deadline_test import config as single
from delayed_whole_test import decide,fresh
from whole_call_test import event
from moe_ub_cosim.whole_call import configuration,logical_finish
from moe_ub_cosim.scaleout_service import Frontend,make_wave
from moe_ub_cosim.delayed_whole import audit_standby


def config():
    c=single();c['workload']['decode_steps']=2
    c['whole_call_replication']['schema_version']='moe-whole-call-replication/4'
    return c


def submit(c,r,w,index,step,now):w.register(make_wave(c,c['parallel']['groups'][index],index,step,1,r),now)


class SequentialChecks(unittest.TestCase):
    def test_prefix_audit_uses_next_step_name_not_always_step_zero(self):
        c,r,w=fresh(config());self.assertEqual(w.next_graphs([0,2]),['g0.w0','g2.w0'])
        submit(c,r,w,0,0,0);submit(c,r,w,2,0,0);w.accept(event('g0.w0',60,1))
        groups,_=w.choose(decide(60,1));self.assertEqual(w.next_graphs(groups),['g0.w1'])
        self.assertNotEqual(w.next_graphs(groups),['g0.w0'])
        submit(c,r,w,0,1,60)
        with self.assertRaises(ValueError):w.next_graphs([0])

    def test_different_segment_lengths_have_different_input_identity(self):
        c=config();_,_,a=fresh(c);c=deepcopy(c);c['workload']['decode_steps']=3;_,_,b=fresh(c)
        self.assertNotEqual(a.snapshot()['calls']['target']['input_identity'],b.snapshot()['calls']['target']['input_identity'])

    def test_two_completed_steps_before_deadline_skip_replica_without_any_waves(self):
        c,r,w=fresh(config());submit(c,r,w,0,0,0);submit(c,r,w,2,0,0)
        w.accept(event('g0.w0',30,1));submit(c,r,w,0,1,30)
        w.accept(event('g0.w1',60,2));self.assertEqual(w.choose(decide(60,1)),([],120))
        replica=w.snapshot()['calls']['target']['attempts']['replica']
        self.assertEqual(replica['waves'],[]);self.assertEqual(replica['skipped_ns'],60)
        self.assertIsNone(replica['finished_ns'])

    def test_intermediate_retirement_is_not_result_or_skip(self):
        c,r,w=fresh(config());self.assertEqual(w.choose(decide()),([0,2],70))
        submit(c,r,w,0,0,0);submit(c,r,w,2,0,0);w.accept(event('g0.w0',60,1))
        self.assertEqual(w.choose(decide(60,1)),([0],70))
        self.assertIsNone(w.snapshot()['calls']['target']['result_ready_ns'])
        self.assertIsNone(w.snapshot()['calls']['target']['attempts']['replica']['skipped_ns'])
        submit(c,r,w,0,1,60)
        self.assertEqual(w.choose(decide(70,2,wake=70)),([1],120))

    def test_last_step_commits_once_and_loser_continues_after_winner(self):
        c,r,w=fresh(config());submit(c,r,w,0,0,0);submit(c,r,w,2,0,0)
        w.accept(event('g0.w0',60,1));submit(c,r,w,0,1,60)
        w.accept(event('g2.w0',65,2));submit(c,r,w,2,1,65)
        submit(c,r,w,1,0,70);w.accept(event('g0.w1',80,3));w.choose(decide(80,1))
        w.accept(event('g1.w0',90,4));self.assertEqual(w.choose(decide(90,2)),([1],120))
        submit(c,r,w,1,1,90);w.accept(event('g2.w1',100,5));w.choose(decide(100,3))
        self.assertFalse(w.snapshot()['all_attempts_retired']);w.accept(event('g1.w1',110,6))
        report=w.snapshot();self.assertTrue(report['all_attempts_retired'])
        self.assertEqual(report['calls']['target']['winner'],'primary')
        self.assertEqual(sum(e['kind']=='client_result_ready' for e in w.events),2)
        history={'g0.w0':(0,60),'g0.w1':(60,80),'g2.w0':(0,65),'g2.w1':(65,100),'g1.w0':(70,90),'g1.w1':(90,110)}
        self.assertEqual(logical_finish(report,history),100)
        for kind in ('early_result','missing_step','early_step','late_step','foreign_step','short_sequence'):
            bad=deepcopy(report);h=deepcopy(history);a=bad['calls']['target']['attempts']['primary']
            if kind=='early_result':bad['calls']['target']['result_ready_ns']=60
            elif kind=='missing_step':h.pop('g0.w1')
            elif kind=='early_step':a['waves'][1]['submitted_ns']=59
            elif kind=='late_step':a['waves'][1]['submitted_ns']=61
            elif kind=='foreign_step':a['waves'][1]['graph_id']='g2.w1'
            else:bad['decode_steps']=1
            with self.subTest(kind=kind),self.assertRaises(ValueError):logical_finish(bad,h)

    def test_duplicate_out_of_order_and_early_graphs_are_rejected(self):
        for kind in ('skip_first','duplicate_first','early_second','duplicate_callback','foreign_callback'):
            c,r,w=fresh(config())
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                if kind=='skip_first':submit(c,r,w,0,1,0)
                else:
                    submit(c,r,w,0,0,0)
                    if kind=='duplicate_first':submit(c,r,w,0,0,0)
                    elif kind=='early_second':submit(c,r,w,0,1,10)
                    elif kind=='foreign_callback':w.accept(event('g0.w1',10,1))
                    else:w.accept(event('g0.w0',10,1));w.accept(event('g0.w0',10,2))

    def test_old_formats_keep_single_step_restriction(self):
        for version in (1,2,3):
            c=config();o=c['whole_call_replication'];o['schema_version']=f'moe-whole-call-replication/{version}'
            if version<3:
                for call in o['calls']:call.pop('hedge_after_ns')
                if version==2:o['hedge_after_ns']=70
                else:o['policy']='eager_complete_segment/1';o.pop('max_wakeup_events')
            with self.subTest(version=version),self.assertRaises(ValueError):configuration(c)
        for steps in (0,True,129):
            c=config();c['workload']['decode_steps']=steps
            with self.subTest(steps=steps),self.assertRaises(ValueError):configuration(c)

    def test_real_scheduler_keeps_KV_after_step_one_and_releases_only_after_final_step(self):
        c,r,w=fresh(config())
        class Preflight:
            def accept_wave(self,wave,now):pass
        f=Frontend(c,8,1,r,message_preflight=Preflight());f.whole=w;transcript=[]
        with tempfile.TemporaryDirectory() as folder,patch('moe_ub_cosim.scaleout_service.export_wave'):
            def step(e):
                e['available_rank_ids']=list(range(8));reply=f.decide(e,Path(folder),None)
                for p in reply['submissions']:
                    index,number=Path(p).name.split('.');submit(c,r,w,int(index[1:]),int(number[1:]),e['now_ns'])
                transcript.append(dict(event=e,reply=reply));return reply
            def complete(graph,now,seq):
                w.accept(event(graph,now,seq));return step(decide(now,len(transcript),[dict(graph_id=graph,finished_ns=now)]))
            step(decide());complete('g0.w0',40,1);complete('g2.w0',50,2)
            self.assertFalse(f.standby_releases);self.assertFalse(f.skipped_groups)
            step(decide(70,3,wake=70));complete('g1.w0',90,3)
            self.assertFalse(f.standby_releases)
            self.assertGreater(sum(s.kv.npu_pool.used_bytes() for rank,s in f.schedulers.items() if rank in c['parallel']['groups'][1]),0)
            complete('g2.w1',100,4);complete('g0.w1',110,5);last=complete('g1.w1',130,6)
            self.assertTrue(last['service_finished']);self.assertEqual([v['group_index'] for v in f.standby_releases],[3,1])
            self.assertEqual([v['time_ns'] for v in f.standby_releases],[100,130])
            self.assertTrue(all(s.kv.npu_pool.used_bytes()==0 for s in f.schedulers.values()))
            self.assertTrue(all(len(q.itl)==2 for q in f.requests if q.id not in f.skipped_requests))
            audit_standby(c,r,dict(transcript=transcript,whole_call_replication=w.snapshot(),standby_memory=dict(
                initial_reserved_bytes=f.standby_initial_bytes,releases=f.standby_releases,final_used_bytes=0)))


if __name__=='__main__':unittest.main()
