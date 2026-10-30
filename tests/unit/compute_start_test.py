"""Actual queue start semantics, including a queued original contribution."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from contribution_binding_test import config, wave, events, Routes
from moe_ub_cosim.contribution_binding import ContributionBinding
from moe_ub_cosim.execution_events import ExecutionObserver
from moe_ub_cosim.inflight_helpers import audit_compute
from moe_ub_cosim.service_transport import native_arguments
from moe_ub_cosim.scaleout_service import validate


def inputs():
    c=config();c['execution_events']['schema_version']='moe-execution-events/2'
    w=wave();w['shared_compute']=dict(mode='serial_per_gpu/1')
    return c,w


def queued_events():
    _,w=inputs();spec={(int(r),n['id']):n for r,rows in w['rank_nodes'].items() for n in rows}
    rows=[]
    for i,event in enumerate(events()):
        e=dict(event,schema_version='moe-execution-event/2')
        if e['time_ns']>=8:e['time_ns']+=1
        rows.append((e['time_ns'],i,e))
        if e['kind']=='node_issue' and spec[e['rank'],e['node']]['kind']=='COMP':
            start=dict(e,kind='node_compute_start')
            if e['node']==3:start['time_ns']+=1
            rows.append((start['time_ns'],i+.5,start))
    return [dict(e,sequence=i) for i,(_,__,e) in enumerate(sorted(rows,key=lambda x:x[:2]))]


class ComputeStartChecks(unittest.TestCase):
    def runtime(self):
        c,w=inputs();b=ContributionBinding(c,Routes(),ExecutionObserver(c['execution_events']))
        b.register(w);return b

    def test_queued_original_not_computing_at_issue_and_ack_remains_visible(self):
        b=self.runtime()
        for e in queued_events():
            if e['time_ns']>6:break
            b.accept(e)
        s=next(b.records())['state']
        self.assertIsNone(s['attempts']['original.r1']['compute_begin_ns'])
        self.assertEqual(b.observer.nodes['g0.w0',1,3]['issue'],6)
        self.assertEqual(b.observer.messages[7]['send_complete'],6)
        for e in queued_events()[b.observer.count:]:b.accept(e)
        for row in b.records():
            self.assertEqual(row['state']['attempts']['original.r1']['compute_begin_ns'],7)
            self.assertEqual(row['state']['attempts']['original.r1']['compute_done_ns'],9)
            self.assertTrue(row['state']['declared_call_retirable'])
        self.assertEqual(b.observer.snapshot()['compute_starts'],6)

    def test_missing_early_late_duplicate_and_wrong_node_start_rejected(self):
        for mutation in ('missing','early','late','duplicate','send','gate','before_issue','wrong_rank','schema'):
            b=self.runtime();rows=queued_events()
            target=next(i for i,e in enumerate(rows) if e['kind']=='node_compute_start' and e['rank']==1 and e['node']==3)
            if mutation=='missing':rows.pop(target)
            elif mutation=='early':rows[target]['time_ns']=5
            elif mutation=='late':rows[target]['time_ns']=8
            elif mutation=='duplicate':rows.insert(target+1,dict(rows[target]))
            elif mutation=='send':rows[target]['node']=2
            elif mutation=='gate':
                b.observer.nodes['g0.w0',1,3]['spec']['kind']='GATE'
            elif mutation=='before_issue':
                start=rows.pop(1);rows.insert(0,start)
            elif mutation=='wrong_rank':rows[target]['rank']=12
            else:rows[target]['schema_version']='moe-execution-event/1'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                for i,e in enumerate(rows):b.accept(dict(e,sequence=i))

    def test_rejected_start_does_not_mutate_observer_or_contribution(self):
        b=self.runtime();rows=queued_events();b.accept(rows[0]);before=deepcopy(b.observer.__dict__)
        state=next(b.records())['state']
        with self.assertRaises(ValueError):b.accept(dict(rows[1],rank=1,node=3))
        self.assertEqual(b.observer.__dict__,before);self.assertEqual(next(b.records())['state'],state)

    def test_preflight_includes_all_comp_starts_across_graphs(self):
        c,w=inputs();c['execution_events']['max_events']=35
        o=ExecutionObserver(c['execution_events'])
        with self.assertRaisesRegex(ValueError,'budget'):o.register(w)
        self.assertFalse(o.nodes)
        c['execution_events']['max_events']=36;o=ExecutionObserver(c['execution_events']);o.register(w)
        w2=deepcopy(w);w2['graph_id']='second'
        for m in w2['messages']:m['id']+=10
        for rows in w2['rank_nodes'].values():
            for n in rows:
                if 'message' in n:n['message']['id']+=10
        with self.assertRaisesRegex(ValueError,'budget'):o.register(w2)
        self.assertEqual(len(o.graphs),1)

    def test_v2_requires_shared_wave_and_old_mode_refuses_new_event(self):
        c,w=inputs();del w['shared_compute']
        with self.assertRaisesRegex(ValueError,'FIFO'):ExecutionObserver(c['execution_events']).register(w)
        old=ExecutionObserver(config()['execution_events']);old.register(wave())
        with self.assertRaises(ValueError):old.accept(queued_events()[1])
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(native_arguments(dict(execution_events=c['execution_events']),Path(d)),['--execution-events-v2','128'])

    def test_service_rejects_v2_without_shared_compute_before_topology(self):
        c=dict(schema_version='moe-scaleout-service/1',model={},workload={},parallel={},compute={},
               execution_events=dict(schema_version='moe-execution-events/2',max_events=128))
        with self.assertRaisesRegex(ValueError,'shared inflight helper compute'):validate(c,{}, {})

    def test_independent_shared_report_must_match_actual_start(self):
        b=self.runtime()
        for e in queued_events():b.accept(e)
        tasks=[]
        for (g,r,n),row in b.observer.nodes.items():
            if row['spec']['kind']=='COMP':
                tasks.append(dict(kind='original',graph_id=g,rank=r,node=n,sequence=len(tasks),
                    ready_ns=row['issue'],start_ns=row['compute_start'],done_ns=row['complete'],duration_ns=row['spec']['duration_ns']))
        result=dict(gpu_count=2,finish_ns=21,service=dict(shared_compute=dict(mode='serial_per_gpu/1',empty=True,tasks=tasks)))
        # Explicit intervening work explains the one-ns queued expert interval.
        for r in (0,1):
            tasks.append(dict(kind='helper_experts',helper_id=str(r),rank=r,sequence=tasks[3*r+1]['sequence'],
                ready_ns=6,start_ns=6,done_ns=7,duration_ns=1))
        for r in (0,1):
            for i,t in enumerate(sorted((t for t in tasks if t['rank']==r),key=lambda t:(t['start_ns'],t['node'] if 'node' in t else -1))):
                t['sequence']=2*i+r
        audit_compute(result,b.observer)
        b.observer.nodes['g0.w0',0,3]['compute_start']=6
        with self.assertRaisesRegex(ValueError,'original start differs'):audit_compute(result,b.observer)


if __name__=='__main__':unittest.main()
