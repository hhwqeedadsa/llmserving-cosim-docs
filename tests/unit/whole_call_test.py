"""Explicit source-derived attempts and complete-result boundaries; no network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.whole_call import WholeCallConsumer,configuration,logical_finish
from moe_ub_cosim.service_terminal import service_terminal
from service_terminal_test import completed


def config():
    c=json.loads((ROOT/'examples/scaleout_route_replay/service.json').read_bytes())
    c['workload']['decode_steps']=1;c['parallel']['groups']=[[0,2],[1,3]]
    c['route_replay']['request_layout']='sparse_explicit/1'
    c['route_replay']['requests']=[q for q in c['route_replay']['requests'] if q['rank_id'] in (0,2)]
    c['phase_completion']=dict(mode='ep_transport_flush/1');c['transport']={}
    c['token_transport']=dict(mode='deepep_direct_bf16/1',combine='rank_reduced/1',local_reduce_ns_per_extra=1)
    c['execution_events']=dict(schema_version='moe-execution-events/1',max_events=1024)
    c['whole_call_replication']=dict(schema_version='moe-whole-call-replication/1',policy='eager_complete_segment/1',
        initial_state='declared_equivalent_ready_kv/1',max_events=7,calls=[dict(id='c0',primary_group=0,replica_group=1)])
    return c


def event(graph,now,sequence):
    return dict(schema_version='moe-execution-event/1',type='OBSERVE',kind='graph_retired',
                graph_id=graph,time_ns=now,sequence=sequence)


def runtime():
    c=config();r=Replay(c,ROOT);consumer=WholeCallConsumer(c,r)
    for i,g in enumerate(c['parallel']['groups']):consumer.register(make_wave(c,g,i,0,1,r),0)
    return c,r,consumer


class WholeCallChecks(unittest.TestCase):
    def test_two_originals_make_four_explicit_attempts_not_four_independent_sources(self):
        c,r,consumer=runtime()
        self.assertEqual(len(r.selected['files']),2);self.assertEqual(len(r.records),4)
        self.assertEqual(r.records[1]['replica_of'],0);self.assertEqual(r.records[3]['replica_of'],2)
        self.assertEqual(consumer.snapshot()['logical_requests'],2)
        self.assertEqual(consumer.snapshot()['execution_attempt_requests'],4)
        for role,g in enumerate(c['parallel']['groups']):
            selected=r.routes(g,0,0)
            self.assertEqual([t['experts'] for t in selected],[[0,3],[0,2]])
            self.assertEqual([t['source']['request_id'] for t in selected],g)

    def test_snapshot_copies_only_unique_raws_and_rebuilds_lineage(self):
        c,r,consumer=runtime()
        with tempfile.TemporaryDirectory() as folder:
            saved,evidence=r.snapshot(folder);restored=Replay(saved,folder)
            self.assertEqual(evidence['raw_snapshots'],2);self.assertEqual(evidence['logical_requests'],2)
            self.assertTrue(evidence['intentional_replica_derivation'])
            self.assertFalse(evidence['equivalent_ready_kv_verified'])
            self.assertEqual(WholeCallConsumer(saved,restored).snapshot(),WholeCallConsumer(c,r).snapshot())
            self.assertEqual(restored.routes([1,3],0,1),r.routes([1,3],0,1))

    def test_partial_completion_does_not_submit_result_and_loser_still_runs(self):
        _,_,consumer=runtime()
        consumer.accept(dict(kind='node_complete',time_ns=5))
        self.assertIsNone(consumer.snapshot()['result_ready_ns'])
        consumer.accept(event('g1.w0',10,100))
        state=consumer.snapshot();self.assertEqual(state['calls']['c0']['winner'],'replica')
        self.assertEqual(state['result_ready_ns'],10);self.assertFalse(state['all_attempts_retired'])
        self.assertIsNone(state['calls']['c0']['attempts']['primary']['finished_ns'])
        consumer.accept(event('g0.w0',30,200))
        self.assertTrue(consumer.snapshot()['all_attempts_retired']);self.assertEqual(len(consumer.events),7)
        self.assertEqual(sum(e['kind']=='client_result_ready' for e in consumer.events),1)
        self.assertEqual(logical_finish(consumer.snapshot(),{'g0.w0':(0,30),'g1.w0':(0,10)}),10)

    def test_original_first_and_simultaneous_results_follow_actual_event_order(self):
        for first in ('g0.w0','g1.w0'):
            _,_,consumer=runtime();second='g1.w0' if first=='g0.w0' else 'g0.w0'
            consumer.accept(event(first,10,100));consumer.accept(event(second,10,101))
            winner='primary' if first=='g0.w0' else 'replica'
            self.assertEqual(consumer.snapshot()['calls']['c0']['winner'],winner)

    def test_multiple_logical_calls_finish_only_when_each_has_a_complete_winner(self):
        c=config();c['parallel']['groups']=[[0,2],[1,3],[4,6],[5,7]]
        c['route_replay']['requests'][1]['rank_id']=4
        c['whole_call_replication']['calls'].append(dict(id='c1',primary_group=2,replica_group=3))
        c['whole_call_replication']['max_events']=14
        r=Replay(c,ROOT);consumer=WholeCallConsumer(c,r)
        for i,g in enumerate(c['parallel']['groups']):consumer.register(make_wave(c,g,i,0,1,r),0)
        consumer.accept(event('g3.w0',5,100));self.assertIsNone(consumer.snapshot()['result_ready_ns'])
        consumer.accept(event('g0.w0',10,200));self.assertEqual(consumer.snapshot()['result_ready_ns'],10)
        self.assertFalse(consumer.snapshot()['all_attempts_retired'])
        consumer.accept(event('g2.w0',20,300));consumer.accept(event('g1.w0',30,400))
        self.assertEqual(consumer.snapshot()['lifecycle_events'],14)

    def test_invalid_duplicate_early_and_wrong_epoch_result_do_not_mutate(self):
        for mutation in ('time','sequence','foreign','schema','bool'):
            _,_,consumer=runtime();consumer.accept(event('g1.w0',10,100));before=deepcopy(consumer.__dict__)
            row=event('g0.w0',30,200)
            if mutation=='time':row['time_ns']=9
            elif mutation=='sequence':row['sequence']=100
            elif mutation=='foreign':row['graph_id']='g0.w1'
            elif mutation=='schema':row['schema_version']='moe-execution-event/2'
            else:row['time_ns']=True
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):consumer.accept(row)
            self.assertEqual(consumer.__dict__,before)
        _,_,consumer=runtime();consumer.accept(event('g0.w0',10,100))
        with self.assertRaises(ValueError):consumer.accept(event('g0.w0',11,101))

    def test_unknown_or_second_submission_and_non_eager_start_rejected(self):
        c,r,consumer=runtime();w=make_wave(c,c['parallel']['groups'][0],0,0,1,r)
        with self.assertRaises(ValueError):consumer.register(w,0)
        fresh=WholeCallConsumer(c,r)
        with self.assertRaises(ValueError):fresh.register(w,1)
        w['graph_id']='g0.w1'
        with self.assertRaises(ValueError):fresh.register(w,0)
        self.assertFalse(fresh.events)

    def test_default_no_cloning_rule_and_replica_source_restrictions_remain(self):
        c=config();del c['whole_call_replication']
        with self.assertRaises(ValueError):Replay(c,ROOT)
        c=config();c['route_replay']['requests'][1]['file_id']=c['route_replay']['requests'][0]['file_id']
        with self.assertRaises(ValueError):Replay(c,ROOT)
        c=config();c['route_replay']['requests'][1]['rank_id']=1
        with self.assertRaises(ValueError):Replay(c,ROOT)

    def test_strict_policy_scope_initial_state_budget_and_group_partition(self):
        self.assertIsNone(configuration({}))
        for change in ('policy','state','budget','group','overlap','steps','bool_steps','helper','extra'):
            c=config();o=c['whole_call_replication']
            if change=='policy':o['policy']='late_retry/1'
            elif change=='state':o['initial_state']='already_verified'
            elif change=='budget':o['max_events']=6
            elif change=='group':o['calls'][0]['replica_group']=0
            elif change=='overlap':c['parallel']['groups'][1]=[0,3]
            elif change=='steps':c['workload']['decode_steps']=2
            elif change=='bool_steps':c['workload']['decode_steps']=True
            elif change=='helper':c['inflight_helpers']={}
            else:o['future_truth']=1
            with self.subTest(change=change),self.assertRaises(ValueError):configuration(c)

    def test_logical_terminal_is_first_full_attempt_not_final_cleanup_commit(self):
        c,_,consumer=runtime();consumer.accept(event('g1.w0',10,100));consumer.accept(event('g0.w0',30,200))
        r,f=completed()
        f['history']=[dict(graph_id='g0.w0',start_ns=0,finished_ns=30),dict(graph_id='g1.w0',start_ns=0,finished_ns=10)]
        r['service']['waves']=deepcopy(f['history'])
        f['transcript'][-1]['event']['completed']=[dict(graph_id='g0.w0',finished_ns=30)]
        f['whole_call_replication']=consumer.snapshot()
        t=service_terminal(r,f,require_complete=True,config=c)
        self.assertEqual((t['application_finished_ns'],t['attempts_finished_ns'],t['network_drained_ns']),(10,30,50))
        for bad in ('early','missing','winner','unfinished'):
            changed=deepcopy(f)
            if bad=='early':changed['whole_call_replication']['calls']['c0']['result_ready_ns']=9
            elif bad=='missing':changed['whole_call_replication']['calls']['c0']['attempts']['primary']['graph_id']='other'
            elif bad=='winner':changed['whole_call_replication']['calls']['c0']['winner']='primary'
            else:changed['whole_call_replication']['all_attempts_retired']=False
            with self.subTest(bad=bad),self.assertRaises(ValueError):service_terminal(r,changed,config=c)
        with self.assertRaisesRegex(ValueError,'explicit matching input'):service_terminal(r,f)


if __name__=='__main__':unittest.main()
