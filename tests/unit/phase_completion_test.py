"""Completion edges and actual join times, including tampered graph/evidence."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest

from moe_ub_cosim.phase_completion import enabled,structure,actual
from moe_ub_cosim.scaleout_rdma import ROOT
from moe_ub_cosim.scaleout_replay import Replay
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.service_transport import native_arguments


class PhaseChecks(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'examples/scaleout_route_replay/service.json').read_bytes())
        self.old=make_wave(self.c,[0,1,2,3],0,0,1,Replay(self.c,ROOT))
        self.c['phase_completion']=dict(mode='ep_transport_flush/1')
        self.wave=make_wave(self.c,[0,1,2,3],0,0,1,Replay(self.c,ROOT))

    def test_default_has_identical_messages_and_explicit_mode_has_full_membership(self):
        self.assertEqual(self.wave['messages'],self.old['messages'])
        self.assertEqual(self.wave['routes'],self.old['routes'])
        self.assertEqual(len(structure(self.wave,self.c)),4)
        self.assertEqual(sum(n['kind']=='GATE' for tasks in self.wave['rank_nodes'].values() for n in tasks),16)
        c=deepcopy(self.c);del c['phase_completion'];self.assertFalse(enabled(c))
        self.assertEqual(structure(self.old,c),[])

    def test_missing_member_predecessor_consumer_and_old_graph_reject(self):
        for change in ('member','producer','send','recv','consumer','extra','old','mode'):
            w=deepcopy(self.wave);c=deepcopy(self.c);gate=w['phase_completion']['gates'][0]
            member=gate['members'][0];tasks=w['rank_nodes'][member['rank']];task=next(n for n in tasks if n['id']==member['node'])
            if change=='member':gate['members'].pop()
            if change=='producer':task['deps'].remove(next(n['id'] for n in tasks if n['name']=='L0.pack'))
            if change in ('send','recv'):
                node=next(n for n in tasks if n['kind']==change.upper() and n['message']['layer']==0 and n['message']['phase']=='dispatch')
                task['deps'].remove(node['id'])
            if change=='consumer':next(n for n in tasks if n['name']=='L0.experts')['deps'].remove(task['id'])
            if change=='extra':gate['members'][1]['node']=member['node']
            if change=='old':w=self.old
            if change=='mode':c['phase_completion']['mode']='guess_completion'
            with self.subTest(change=change),self.assertRaises((ValueError,KeyError)):structure(w,c)

    def test_empty_local_source_rank_still_joins_all_phases(self):
        c=deepcopy(self.c);c['route_replay']['request_layout']='sparse_explicit/1';c['route_replay']['requests']=c['route_replay']['requests'][:1]
        wave=make_wave(c,[0,1,2,3],0,0,1,Replay(c,ROOT));structure(wave,c)
        for gate in wave['phase_completion']['gates']:self.assertEqual([m['rank'] for m in gate['members']],[0,1,2,3])

    def evidence(self):
        events={};rows=[];graph=self.wave['graph_id']
        for index,gate in enumerate(self.wave['phase_completion']['gates']):
            members=[]
            for m in gate['members']:
                begin=index*100+m['rank']
                events[graph,m['rank'],m['node'],'issue']=begin
                events[graph,m['rank'],m['node'],'callback']=index*100+3
                members.append(dict(m,arrived_ns=begin))
            rows.append(dict(graph_id=graph,id=gate['id'],released_ns=index*100+3,members=members))
        return dict(service=dict(phase_completion=dict(mode='ep_transport_flush/1',gates=rows))),events

    def test_last_actual_arrival_controls_release_and_tampering_rejects(self):
        result,events=self.evidence();actual([self.wave],self.c,result,events)
        for change in ('early','late-callback','duplicate','missing','arrival'):
            r=deepcopy(result);e=deepcopy(events);rows=r['service']['phase_completion']['gates']
            if change=='early':rows[0]['released_ns']-=1
            if change=='late-callback':e[self.wave['graph_id'],0,self.wave['phase_completion']['gates'][0]['members'][0]['node'],'callback']+=1
            if change=='duplicate':rows[-1]=deepcopy(rows[0])
            if change=='missing':rows.pop()
            if change=='arrival':rows[0]['members'][0]['arrived_ns']+=1
            with self.subTest(change=change),self.assertRaises(ValueError):actual([self.wave],self.c,r,e)

    def test_backend_option_is_explicit_before_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(native_arguments(self.c,Path(folder)),['--phase-completion','ep_transport_flush/1'])


if __name__=='__main__':unittest.main()
