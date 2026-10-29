"""Sparse request conservation, input rejection and switch-only reachability."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_connectivity import validate_groups
from moe_ub_cosim.scaleout_service import make_wave
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.simai_service import validate

ROOT=Path(__file__).resolve().parents[2]
EXAMPLE=ROOT/'examples/simai_service'


class SimaiChecks(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((EXAMPLE/'service.json').read_text())
        self.p=json.loads((EXAMPLE/'rdma.json').read_text())
        self.t,_=generate(json.loads((EXAMPLE/'topology.json').read_text()))

    def test_sparse_sources_do_not_create_idle_attention(self):
        n,planes,replay=validate(self.c,self.t,self.p,EXAMPLE)
        self.assertEqual((n,planes),(64,1))
        wave=make_wave(self.c['service'],list(range(64)),0,0,planes,replay)
        active={0,32};received={}
        for record in replay.records.values():
            for expert in record['choices'][0][0]:
                dst=expert//4;received[dst]=received.get(dst,0)+1
        for rank,tasks in wave['rank_nodes'].items():
            costs={task['name']:task['duration_ns'] for task in tasks if task['kind']=='COMP'}
            self.assertEqual(costs['L0.attention'],1000 if rank in active else 0)
            self.assertEqual(costs['L0.experts'],received.get(rank,0)*100)
        self.assertTrue(set(received)-active)
        self.assertEqual(sum(m['bytes'] for m in wave['messages']),
                         2*sum(e//4!=r['rank_id'] for r in replay.records.values() for e in r['choices'][0][0])*14336)

    def test_source_identity_and_bounds_rejection(self):
        for name in ('duplicate-file','duplicate-slot','hash','eof','context','clone','wrong-model'):
            c=deepcopy(self.c);requests=c['routes']['requests']
            if name=='duplicate-file':requests[1]['file_id']=requests[0]['file_id']
            if name=='duplicate-slot':requests[1]['rank_id']=0
            if name=='hash':requests[0]['file']['sha256']='0'*64
            if name=='eof':requests[0]['first_decode_entry']=2
            if name=='context':c['service']['workload']['context_tokens']=16
            if name=='clone':requests[1]['file']=deepcopy(requests[0]['file'])
            if name=='wrong-model':c['service']['model']['name']='other/model'
            with self.subTest(name=name),self.assertRaises(ValueError):validate(c,self.t,self.p,EXAMPLE)

    def test_snapshot_can_move_without_original_paths(self):
        _,_,replay=validate(self.c,self.t,self.p,EXAMPLE)
        with tempfile.TemporaryDirectory() as folder:
            c=replay.snapshot(folder)
            _,_,moved=validate(c,self.t,self.p,folder)
            self.assertEqual(moved.routes(list(range(64)),1,0),replay.routes(list(range(64)),1,0))

    def test_components_agree_with_independent_pair_bfs(self):
        # Independent pair BFS, rather than comparing two callers of the same
        # component checker. Remove upper links to split the two rails.
        config=json.loads((ROOT/'examples/scaleout_multirail_native/topology.json').read_text())
        t,_=generate(config)
        protocol=deepcopy(self.p)
        protocol['ecn_by_rate']={str(e['rate_bps']):dict(kmin_bytes=32768,kmax_bytes=131072,pmax=1.0) for e in t['links']}
        switches={n['id'] for n in t['nodes'] if n['kind']!='gpu'}
        for disconnected in (False,True):
            topo=deepcopy(t)
            if disconnected:topo['links']=[e for e in topo['links'] if not (e['src'] in switches and e['dst'] in switches)]
            by_id={n['id']:n for n in topo['nodes']};adjacency={n:[] for n in by_id}
            for edge in topo['links']:
                adjacency[edge['src']].append(edge['dst']);adjacency[edge['dst']].append(edge['src'])
            ranks=[n['id'] for n in topo['nodes'] if n['kind']=='gpu']
            for a in ranks:
                for b in ranks:
                    if a>=b:continue
                    old=True
                    for plane in sorted({n['plane'] for n in topo['nodes'] if n['kind']!='gpu'}):
                        seen={a};todo=[a]
                        while todo:
                            for peer in adjacency[todo.pop()]:
                                if peer in seen:continue
                                if peer==b:seen.add(peer)
                                elif by_id[peer]['kind']!='gpu' and by_id[peer]['plane']==plane:
                                    seen.add(peer);todo.append(peer)
                        old=old and b in seen
                    try:validate_groups(topo,protocol,[[a,b]]);new=True
                    except ValueError:new=False
                    self.assertEqual(new,old,(a,b,disconnected))


if __name__=='__main__':unittest.main()
