"""Hand-calculated input and statistics checks; no simulation fixtures."""
import ast
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

from experiments.scaleup_observability.common import HERE
from experiments.scaleup_observability.inputs import make_input, prepare, requests, verified_case
from experiments.scaleup_observability.overview import transfer_rows
from experiments.scaleup_observability.statistics import dependency_rows, distribution, queue_summary
from experiments.scaleup_observability.workflow import storage


def state(t,v,f=0,kind='update'):
    return dict(time_ns=t,voq_waiting_bytes=v,fifo_waiting_bytes=f,event_kind=kind)


class InputTests(unittest.TestCase):
    def test_nested_inputs_and_distinct_sets(self):
        all_ids=[]
        for seed in (0,1,2):
            large=requests(seed,8)
            self.assertEqual(len(large),64)
            self.assertEqual(requests(seed,2),[r for r in large if r['request_id']%8<2])
            self.assertEqual(requests(seed,4),[r for r in large if r['request_id']%8<4])
            self.assertEqual(len({r['request_id'] for r in large}),64)
            all_ids.extend(r['request_id'] for r in large)
            self.assertTrue(all(len(set(x))==2 and all(0<=v<16 for v in x)
                for r in large for x in r['decode_routes'][0].values()))
        self.assertEqual(len(set(all_ids)),192)
        self.assertNotEqual([r['decode_routes'] for r in requests(0,8)],
                            [r['decode_routes'] for r in requests(1,8)])

    def test_workload_limits_costs_and_expert_totals(self):
        profiles=[]
        for batch in (2,4,8):
            service,layers,counts=make_input(0,batch)
            self.assertEqual(len(service['requests']),8*batch)
            self.assertEqual(service['groups'][0]['scheduling']['max_num_seqs'],batch)
            self.assertTrue(all(l['batch_limits']['max_num_batched_tokens']==batch for l in layers['groups'][0]['layers']))
            self.assertTrue(all(sum(c)==16*batch for c in counts.values()))
            profiles.append(service['groups'][0]['full_model_profile'])
        self.assertTrue(all(p==profiles[0] for p in profiles))

    def test_freeze_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'batch'; result=prepare(p)
            self.assertEqual(len(result['cases']),9)
            for cid in result['cases']:verified_case(p,cid)
            file=p/'inputs/s0-b2/service.json'
            file.write_text(file.read_text()+' ')
            with self.assertRaisesRegex(ValueError,'Frozen input differs'):verified_case(p,'s0-b2')

    def test_no_other_experiment_or_test_runtime_import(self):
        for file in HERE.glob('*.py'):
            tree=ast.parse(file.read_text())
            for node in ast.walk(tree):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else ([node.module or ''] if isinstance(node,ast.ImportFrom) else [])
                self.assertFalse(any('mapping_value' in n or n.startswith(('tests','fixtures','scripts')) for n in names),file)


class StatisticsTests(unittest.TestCase):
    def test_queue_area_and_busy_exclusion(self):
        events=[state(0,0,kind='initial'),state(5,10),state(10,0,4),state(15,0),state(20,0,kind='final')]
        result,gaps=queue_summary(events,[(7,11),(13,16)],20)
        self.assertEqual((result['voq_byte_ns'],result['fifo_byte_ns']),(50,20))
        self.assertEqual(result['queue_nonzero_ns'],10)
        self.assertEqual(result['link_busy_ns'],7)
        self.assertEqual(gaps,[[5,7],[11,13]])
        self.assertEqual(result['queued_without_transmission_ns'],4)

    def test_zero_duration_peak_not_area(self):
        events=[state(0,0,kind='initial'),state(5,100),state(5,0),state(10,0,kind='final')]
        result,gaps=queue_summary(events,[],10)
        self.assertEqual(result['voq_peak_bytes'],100)
        self.assertEqual(result['voq_byte_ns'],0)
        self.assertEqual(gaps,[])

    def test_continuing_backlog_and_simulation_clip(self):
        events=[state(0,3,kind='initial'),state(4,2),state(8,0),state(100,0,kind='final')]
        result,gaps=queue_summary(events,[(2,6),(5,7)],10)
        self.assertEqual(result['voq_byte_ns'],20)
        self.assertEqual(result['link_busy_ns'],5)
        self.assertEqual(gaps,[[0,2],[7,8]])

    def test_missing_history_rejected(self):
        with self.assertRaisesRegex(ValueError,'cover execution'):
            queue_summary([state(0,0,kind='initial'),state(3,0,kind='final')],[],4)
        with self.assertRaisesRegex(ValueError,'Unordered'):
            queue_summary([state(0,0,kind='initial'),state(5,1),state(4,2),state(10,0,kind='final')],[],10)

    def test_nearest_rank_quantile_and_empty(self):
        self.assertEqual(distribution([10,1,2,3])['median'],2)
        self.assertEqual(distribution([10,1,2,3])['p95'],10)
        self.assertIsNone(distribution([])['max'])

    def test_dependency_ready_is_not_actual_start(self):
        graph=dict(rank_id=0,nodes=[dict(node_id=1,kind='COMP',duration_ns=5,deps=[]),
                                  dict(node_id=2,kind='COMP',duration_ns=2,deps=[1])])
        execution={(0,1):dict(kind='COMP',start_ns=0,end_ns=5),
                   (0,2):dict(kind='COMP',start_ns=8,end_ns=10)}
        rows=dependency_rows(graph,execution,0)
        self.assertEqual(rows[1]['dependency_ready_ns'],5)
        self.assertEqual(rows[1]['after_dependencies_before_start_ns'],3)
        bad=deepcopy(execution);bad[0,2]['start_ns']=4
        with self.assertRaisesRegex(ValueError,'dependency'):dependency_rows(graph,bad,0)
        bad=deepcopy(execution);bad[0,2]['end_ns']=11
        with self.assertRaisesRegex(ValueError,'Compute duration'):dependency_rows(graph,bad,0)

    def test_load_roles_and_completion_tail(self):
        planned=[dict(transfer_id=1,payload_bytes=512,business_payload_bytes=500,
                      kind='combine',message_role='data',operation_id='op')]
        actual=[dict(transfer_id=1,ub_task_id=7,bytes=512,committed_bytes=512,submitted_tasks=1,
            send_callbacks=1,receive_callbacks=1,context_active=False,initiator_resources_retired=True,
            send_start_ns=10,data_ready_ns=30,transaction_done_ns=35,send_done_ns=35,receive_done_ns=35,
            data_owner_rank=0,consumer_rank=5,initiator_rank=5,operation='LOAD',committed_segments=1)]
        row=transfer_rows(planned,actual,[],{'op':dict(layer_id=1)})[0]
        self.assertEqual((row['data_owner_rank'],row['consumer_rank']),(0,5))
        self.assertEqual(row['data_latency_ns'],20)
        self.assertEqual(row['data_to_protocol_complete_ns'],5)
        self.assertEqual(row['locality'],'cross_server')
        with self.assertRaisesRegex(ValueError,'coverage'):transfer_rows(planned,[],[],{})

    def test_hardlink_budget_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);a=root/'a';b=root/'b';a.mkdir();b.mkdir()
            (a/'file').write_bytes(b'x'*123)
            os.link(a/'file',b/'file')
            self.assertEqual(storage([a,b])['apparent_bytes'],123)
            self.assertEqual(storage([root,a,b]),storage([root]))


if __name__=='__main__':unittest.main()
