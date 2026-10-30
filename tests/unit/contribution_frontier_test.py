"""A fast helper is useful only if the last full-contribution frontier moves."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.contribution_frontier import call_frontier, diagnose, execute


def fixture():
    row=dict(call='g.l0.r0.s0',binding=dict(graph_id='g',source_rank=0,slot=0,logical_layer=0,bundles=[
        dict(id='r1',rank=1,experts=[1],expert_node=3,combine_message=101),
        dict(id='r2',rank=2,experts=[2],expert_node=3,combine_message=102)]),
        spec=dict(expected_experts=[1,2],bundles=[dict(id='r1',experts=[1]),dict(id='r2',experts=[2])],
                  invocation=dict(decode_step=0)),state=dict(accepted_experts=[1,2],logical_complete_ns=20,
            winners={'r1':dict(attempt='helper.r1',time_ns=5),'r2':dict(attempt='original.r2',time_ns=20)},
            attempts={'original.r1':dict(bundle='r1',input_ready_ns=2,result_ready_ns=10),
                      'helper.r1':dict(bundle='r1',input_ready_ns=1,result_ready_ns=5),
                      'original.r2':dict(bundle='r2',input_ready_ns=3,result_ready_ns=20)}))
    messages={101:dict(id=101,src=1,dst=0,bytes=4,receive_ready_ns=10),
              102:dict(id=102,src=2,dst=0,bytes=4,receive_ready_ns=20),
              200:dict(id=200,src=0,dst=3,bytes=4,receive_ready_ns=1),
              201:dict(id=201,src=3,dst=0,bytes=4,receive_ready_ns=5)}
    helper={('g.l0.r0.s0','helper.r1'):201}
    buffers={201:dict(id=201,receive_ready_ns=5,target_released_ns=7),
             102:dict(id=102,receive_ready_ns=20,target_released_ns=25)}
    return row,messages,helper,buffers


class FrontierChecks(unittest.TestCase):
    def test_cli_and_output_protect_saved_run(self):
        from moe_ub_cosim.cli import parser
        args=parser().parse_args(['scaleout','contribution-frontier','--run','a','--output','b'])
        self.assertEqual(args.run,Path('a'))
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)/'run';run.mkdir()
            for output in (run,run/'diagnosis'):
                with self.assertRaises(ValueError):execute(run,output)

    def test_faster_noncritical_helper_does_not_move_whole_frontier(self):
        args=fixture();before=deepcopy(args);r=call_frontier(*args)
        self.assertEqual(r['original_all_ready_ns'],20)
        self.assertEqual(r['winner_all_ready_ns'],20)
        self.assertEqual(r['recorded_frontier_advance_ns'],0)
        self.assertEqual(r['bundles'][0]['individual_advance_ns'],5)
        self.assertEqual(args,before)

    def test_observe_only_service_has_no_invented_helper_benefit(self):
        row,m,_,b=fixture();row['state']['attempts'].pop('helper.r1')
        row['state']['winners']['r1']=dict(attempt='original.r1',time_ns=10)
        r=call_frontier(row,m,{},b)
        self.assertEqual(r['recorded_frontier_advance_ns'],0)
        self.assertTrue(all(d['winner_attempt'].startswith('original.') for d in r['bundles']))

    def test_repair_of_last_contribution_moves_frontier(self):
        row,m,h,b=fixture();row['state']['attempts']['original.r1']['result_ready_ns']=30
        m[101]['receive_ready_ns']=30
        r=call_frontier(row,m,h,b)
        self.assertEqual((r['original_all_ready_ns'],r['winner_all_ready_ns'],r['recorded_frontier_advance_ns']),(30,20,10))

    def test_all_tied_last_contributions_are_preserved(self):
        row,m,h,b=fixture();row['state']['attempts']['original.r1']['result_ready_ns']=20;m[101]['receive_ready_ns']=20
        r=call_frontier(row,m,h,b)
        self.assertEqual(r['original_last_bundles'],['r1','r2'])
        self.assertEqual(r['winner_last_bundles'],['r2'])
        self.assertEqual(r['recorded_frontier_advance_ns'],0)

    def test_helper_buffer_lifetime_is_separate_from_recorded_logical_ready(self):
        row,m,h,b=fixture();r=call_frontier(row,m,h,b)
        self.assertFalse(r['bundles'][0]['winner_buffer_live_at_join'])
        self.assertEqual(r['bundles'][0]['minimum_extra_hold_ns'],13)
        self.assertTrue(r['bundles'][1]['winner_buffer_live_at_join'])
        b[201]['target_released_ns']=20;r=call_frontier(row,m,h,b)
        self.assertIsNone(r['bundles'][0]['winner_buffer_live_at_join'])
        self.assertEqual(r['bundles'][0]['minimum_extra_hold_ns'],0)

    def test_uncaptured_and_local_results_are_not_invented_buffers(self):
        row,m,h,b=fixture();r=call_frontier(row,m,h,{})
        self.assertEqual(r['bundles'][0]['buffer_evidence'],'not_captured')
        self.assertIsNone(r['bundles'][0]['winner_buffer_live_at_join'])
        local=row['binding']['bundles'][1];local.update(rank=0,combine_message=None)
        r=call_frontier(row,m,h,b)
        self.assertEqual(r['bundles'][1]['buffer_evidence'],'local_no_message')

    def test_bad_cover_winner_or_network_identity_rejected(self):
        for bad in ('missing','expert','winner','time','complete','remote','transport','buffer','original'):
            with self.subTest(bad=bad):
                row,m,h,b=fixture()
                if bad=='missing':row['state']['winners'].pop('r2')
                if bad=='expert':row['binding']['bundles'][0]['experts']=[2]
                if bad=='winner':row['state']['winners']['r1']['attempt']='original.r1'
                if bad=='time':row['state']['attempts']['original.r1']['result_ready_ns']=True
                if bad=='complete':row['state']['logical_complete_ns']=5
                if bad=='remote':row['binding']['bundles'][1]['combine_message']=None
                if bad=='transport':m[201]['dst']=1
                if bad=='buffer':b[201]['receive_ready_ns']=6
                if bad=='original':m.pop(101)
                with self.assertRaises(ValueError):call_frontier(row,m,h,b)

    def test_group_dependency_and_fifo_are_distinct(self):
        row,m,h,b=fixture()
        nodes={'0':[dict(id=10,name='L0.merge',deps=[])],
               **{str(rank):[dict(id=1,name='recv',deps=[]),dict(id=2,name='gate',deps=[1]),
                             dict(id=3,name='L0.experts',deps=[1,2])] for rank in (1,2)}}
        tasks=[dict(kind='original',graph_id='g',rank=rank,node=3,ready_ns=6,start_ns=8,done_ns=9) for rank in (1,2)]
        tasks.append(dict(kind='original',graph_id='g',rank=0,node=10,ready_ns=22,start_ns=24,done_ns=25))
        result=dict(service_transport=dict(messages=list(m.values()),message_buffers=dict(messages=list(b.values()))),
            service=dict(shared_compute=dict(tasks=tasks),inflight_helpers=dict(attempts=[dict(candidate=dict(
                call=row['call'],bundle='r1',input_message=200,result_message=201))]),
                phase_completion=dict(gates=[dict(graph_id='g',id='L0.dispatch',released_ns=6,
                    members=[dict(rank=rank,node=2) for rank in (1,2)])])))
        events=[dict(graph_id='g',rank=rank,node=node,kind=kind,time_ns=time) for rank in (1,2)
                for node,kind,time in [(1,'node_complete',rank+1),(2,'node_complete',6),(3,'node_issue',6)]]
        summary,rows=diagnose([row],result,{'g':dict(decode_step=0,rank_nodes=nodes)},events)
        self.assertEqual(summary['helper_actual_logical_bytes'],8)
        self.assertEqual(summary['bundle_uses_of_strict_dispatch_gate'],2)
        self.assertEqual(rows[0]['bundles'][0]['expert_fifo_wait_ns'],2)
        self.assertEqual(rows[0]['winner_to_merge_start_ns'],4)
        with self.assertRaises(ValueError):diagnose([row,row],result,{'g':dict(decode_step=0,rank_nodes=nodes)},events)


if __name__=='__main__':unittest.main()
