"""Graph semantics and source-only observation rejection, with no network."""
from copy import deepcopy
import unittest

from dependencies import expanded,closure,path_to
from analyze import source_status

def wave(step=0,gated=True):
    nodes={}
    for rank,kind in ((0,'SEND'),(4,'RECV')):
        nodes[str(rank)]=[
            dict(id=1,kind='COMP',name='pack',deps=[],duration_ns=1),
            dict(id=2,kind=kind,name='transport',deps=[1] if kind=='SEND' else [],duration_ns=0,message=dict(id=7)),
            dict(id=3,kind='GATE',name='flush',deps=[1,2],duration_ns=0),
            dict(id=4,kind='COMP',name='experts',deps=[3],duration_ns=1),
            dict(id=5,kind='COMP',name='merge',deps=[4],duration_ns=1)]
        if not gated:
            nodes[str(rank)].pop(2);nodes[str(rank)][2]['deps']=[1,2]
    return dict(graph_id=f'g0.w{step}',group_index=0,decode_step=step,rank_ids=[0,4],rank_nodes=nodes,
        phase_completion=dict(gates=[dict(id='dispatch',members=[dict(rank=0,node=3),dict(rank=4,node=3)])] if gated else []))

def source_prefix():
    return dict(graph=dict(graph_id='g0.w0',messages=[dict(id=7,src=0)]),deadline_ns=25,observed_sequence=3,
        observations=[dict(schema_version='moe-execution-event/1',type='OBSERVE',graph_id='g0.w0',kind='message_send_post',sequence=0,time_ns=5,message=7,rank=0)])

class DependencyChecks(unittest.TestCase):
    def test_send_confirmation_remains_a_cross_rank_gate_predecessor(self):
        _,_,children=expanded([wave()]);roots={('g0.w0',0,2)};reached=closure(roots,children)
        self.assertIn(('g0.w0',4,4),reached)
        self.assertNotIn(('g0.w0',4,2),reached)  # ACK is not a RECV dependency.
        self.assertEqual(path_to(roots,('g0.w0',4,5),children)[0],('g0.w0',0,2))

    def test_receive_stall_propagates_through_all_members_and_next_step(self):
        _,_,children=expanded([wave(),wave(1)]);reached=closure({('g0.w0',4,2)},children)
        for graph in ('g0.w0','g0.w1'):
            for rank in (0,4):self.assertIn((graph,rank,5),reached)

    def test_unrelated_requests_are_not_coupled_without_a_gate(self):
        _,_,children=expanded([wave(gated=False)]);reached=closure({('g0.w0',0,2)},children)
        self.assertIn(('g0.w0',0,5),reached);self.assertNotIn(('g0.w0',4,5),reached)

    def test_graph_retirement_still_joins_next_step_without_phase_gates(self):
        _,_,children=expanded([wave(gated=False),wave(1,gated=False)])
        self.assertIn(('g0.w1',4,5),closure({('g0.w0',0,2)},children))

    def test_missing_dependencies_cycles_members_and_sequence_are_rejected(self):
        for kind in ('missing','cycle','member','undeclared_gate','duplicate','step'):
            waves=[wave()]
            if kind=='missing':waves[0]['rank_nodes']['0'][0]['deps']=[999]
            elif kind=='cycle':waves[0]['rank_nodes']['0'][0]['deps']=[5]
            elif kind=='member':waves[0]['phase_completion']['gates'][0]['members'].pop()
            elif kind=='undeclared_gate':waves[0]['phase_completion']['gates']=[]
            elif kind=='duplicate':waves.append(deepcopy(waves[0]))
            else:waves=[wave(1)]
            with self.subTest(kind=kind),self.assertRaises(AssertionError):expanded(waves)

class PrefixChecks(unittest.TestCase):
    def test_actual_post_and_visible_ack_only(self):
        p=source_prefix();self.assertEqual(source_status(p,20)['overdue'],[7])
        p['observations'].append(dict(p['observations'][0],kind='message_send_complete',sequence=1,time_ns=25))
        self.assertEqual(source_status(p,20)['pending'],[])

    def test_unknown_rank_future_time_duplicate_and_ack_without_post_reject(self):
        for kind in ('unknown','rank','future','duplicate','ack','ordering'):
            p=source_prefix();e=p['observations'][0]
            if kind=='unknown':e['message']=8
            elif kind=='rank':e['rank']=4
            elif kind=='future':e['time_ns']=26
            elif kind=='duplicate':p['observations'].append(dict(e,sequence=1))
            elif kind=='ack':e['kind']='message_send_complete'
            else:p['observations'].append(dict(e,kind='message_send_complete',sequence=1,time_ns=4))
            with self.subTest(kind=kind),self.assertRaises(AssertionError):source_status(p,20)

if __name__=='__main__':unittest.main(verbosity=2)
