"""Route admission versus independently enumerated simple switch paths."""
from itertools import combinations
import unittest

from moe_ub_cosim.scaleout_stream_routes import InitialNicRoutes, validate_nic_routes


def topology(gpus, switches, edges):
    nodes = [dict(id=i, kind='gpu') for i in range(gpus)]
    nodes += [dict(id=i, kind='tor', plane=p) for i,p in switches]
    # Explicit port numbering makes endpoint bindings independent of edge order.
    return dict(nodes=nodes, links=[dict(src=a,dst=b,src_port=ap,dst_port=bp) for a,b,ap,bp in edges])


def stream(src_nic=1, dst_nic=1, plane=0):
    return dict(id=7,src=0,dst=1,plane=plane,source_nic=src_nic,destination_nic=dst_nic)


def enumerate_paths(t, source, destination, plane):
    nodes={n['id']:n for n in t['nodes']};paths=[];edges=t['links']
    def visit(path, first):
        current=path[-1]
        if current==destination:
            paths.append((len(path)-1,first));return
        for e in edges:
            if e['src']==current:peer,port=e['dst'],e['src_port']
            elif e['dst']==current:peer,port=e['src'],e['dst_port']
            else:continue
            if peer in path:continue
            n=nodes[peer]
            if peer!=destination and (n['kind']=='gpu' or n['plane']!=plane):continue
            visit(path+[peer],port if current==source else first)
    visit([source],None)
    if not paths:return ()
    shortest=min(n for n,_ in paths)
    return tuple(sorted({port for n,port in paths if n==shortest}))


class NicRouteTests(unittest.TestCase):
    def test_all_64_four_switch_graphs_against_path_enumeration(self):
        possible=list(combinations(range(2,6),2))
        for mask in range(64):
            edges=[(0,2,1,1),(0,3,2,1),(1,4,1,1),(1,5,2,1)]
            edges += [(a,b,10+i,10+i) for i,(a,b) in enumerate(possible) if mask>>i&1]
            t=topology(2,[(i,0) for i in range(2,6)],edges)
            routes=InitialNicRoutes(t)
            for a,b in ((0,1),(1,0)):
                with self.subTest(mask=mask,source=a):
                    self.assertEqual(routes.candidates(a,b,0),enumerate_paths(t,a,b,0))
                    self.assertEqual(routes.candidates(a,b,0),InitialNicRoutes(dict(t,links=list(reversed(t['links'])))).candidates(a,b,0))

    def test_same_plane_dual_rail_is_rejected(self):
        t=topology(2,[(2,0),(3,0)],[(0,2,1,1),(1,2,1,2),(0,3,2,1),(1,3,2,2)])
        with self.assertRaisesRegex(ValueError,r'source 0->1.*candidates \[1, 2\]'):
            validate_nic_routes(t,[stream()])

    def test_reverse_ambiguity_is_not_hidden_by_single_source_port(self):
        t=topology(2,[(i,0) for i in range(2,5)],
            [(0,2,1,1),(2,3,2,2),(2,4,3,2),(1,3,1,1),(1,4,2,1)])
        with self.assertRaisesRegex(ValueError,r'reverse 1->0.*candidates \[1, 2\]'):
            validate_nic_routes(t,[stream()])

    def test_multiple_physical_nics_with_only_one_shortest_route(self):
        t=topology(2,[(i,0) for i in range(2,6)],
            [(0,2,1,1),(0,3,2,1),(2,4,2,2),(3,5,2,2),(5,4,3,3),(1,4,1,1)])
        validate_nic_routes(t,[stream()])
        with self.assertRaisesRegex(ValueError,r'unique shortest NIC 2; candidates \[1\]'):
            validate_nic_routes(t,[stream(src_nic=2)])

    def test_switch_ecmp_with_unique_endpoint_nic_still_allowed(self):
        t=topology(2,[(i,0) for i in range(2,6)],
            [(0,2,1,1),(2,3,2,1),(2,4,3,1),(3,5,2,1),(4,5,2,2),(1,5,1,3)])
        self.assertEqual(len([x for x in t['links'] if x['src']==2]),2)
        validate_nic_routes(t,[stream()])

    def test_dual_plane_and_unreachable_plane(self):
        t=topology(2,[(2,0),(3,1)],[(0,2,1,1),(1,2,1,2),(0,3,2,1),(1,3,2,2)])
        validate_nic_routes(t,[stream(),stream(2,2,1)])
        self.assertEqual(InitialNicRoutes(t).candidates(0,1,2),())

    def test_gpu_cannot_be_used_as_shortcut(self):
        t=topology(3,[(3,0),(4,0)],
            [(0,3,1,1),(3,4,2,2),(1,4,1,1),(0,2,2,1),(2,1,2,2)])
        self.assertEqual(InitialNicRoutes(t).candidates(0,1,0),(1,))
        self.assertEqual(InitialNicRoutes(t).candidates(0,1,0),enumerate_paths(t,0,1,0))

    def test_no_route_is_rejected(self):
        t=topology(2,[(2,0),(3,0)],[(0,2,1,1),(1,3,1,1)])
        with self.assertRaisesRegex(ValueError,r'candidates \[\]'):
            validate_nic_routes(t,[stream()])


if __name__=='__main__':unittest.main()
