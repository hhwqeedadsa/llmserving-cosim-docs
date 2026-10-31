"""Independent labelled-graph oracle; no network or completion prediction."""
from itertools import product
import unittest

from moe_ub_cosim.native_proof_coverage import NativeProofCoverage
from moe_ub_cosim.modern_native import ModernNativeController
from modern_native_test import example, protocol, topology


def ancestors(graph, node):
    """Walk prerequisite edges; a descendant's level never upgrades an edge."""
    found={};pending=[node];visited=set()
    while pending:
        current=pending.pop()
        if current in visited:continue
        visited.add(current)
        for parent,event in graph[current]:
            found[parent]=max(found.get(parent,-1),int(event=='completed'))
            pending.append(parent)
    return found


class NativeProofCoverageTests(unittest.TestCase):
    def test_all_four_node_labelled_dags_and_all_frontiers(self):
        edges=[(node,parent) for node in range(4) for parent in range(node)]
        for labels in product(range(3),repeat=len(edges)):
            graph=[[] for _ in range(4)]
            for (node,parent),label in zip(edges,labels):
                if label:graph[node].append((parent,'applied' if label==1 else 'completed'))
            index=NativeProofCoverage()
            for node,row in enumerate(graph):self.assertEqual(index.append(row),node)
            reach=[ancestors(graph,n) for n in range(4)]
            for n,a,event in product(range(4),range(4),('applied','completed')):
                self.assertEqual(index.covers(n,a,event),reach[n].get(a,-1)>=int(event=='completed'))
            for levels in product(range(3),repeat=4):
                proof=[(n,'applied' if level==1 else 'completed') for n,level in enumerate(levels) if level]
                expected=[(n,e) for n,e in proof if not any(
                    other!=n and reach[other].get(n,-1)>=int(e=='completed') for other,_ in proof)]
                self.assertEqual(index.compact(proof),expected)

    def test_applied_ancestor_does_not_become_completed_via_completed_descendant(self):
        c=NativeProofCoverage();c.append([]);c.append([(0,'applied')]);c.append([(1,'completed')])
        self.assertTrue(c.covers(2,0,'applied'));self.assertFalse(c.covers(2,0,'completed'))
        self.assertEqual(c.compact([(2,'completed'),(0,'completed')]),[(0,'completed'),(2,'completed')])
        self.assertEqual(c.compact([(2,'applied'),(0,'applied')]),[(2,'applied')])

    def test_duplicate_levels_and_input_order_preserve_the_strongest_frontier(self):
        c=NativeProofCoverage();c.append([]);c.append([]);c.append([(0,'completed')])
        pairs=[(1,'applied'),(0,'applied'),(1,'completed'),(2,'applied')]
        self.assertEqual(c.compact(pairs),[(1,'completed'),(2,'applied')])
        self.assertEqual(c.compact(reversed(pairs)),c.compact(pairs))
        self.assertEqual(pairs,[(1,'applied'),(0,'applied'),(1,'completed'),(2,'applied')])

    def test_invalid_append_does_not_create_a_partial_row(self):
        c=NativeProofCoverage();c.append([])
        for proof in ([(0,'completed'),(1,'applied')],[(2,'applied')],[(-1,'completed')],
                      [(True,'applied')],[(0,'visible')]):
            with self.subTest(proof=proof),self.assertRaises(ValueError):c.append(proof)
            self.assertEqual(len(c),1)
        self.assertEqual(c.append([(0,'completed')]),1)
        self.assertTrue(c.covers(1,0,'completed'))
        for action in (lambda:c.compact([(2,'applied')]),lambda:c.covers(0,2,'applied'),
                       lambda:c.covers(0,0,'visible')):
            with self.assertRaises(ValueError):action()

    def test_last_native_id_and_limit_keep_the_complete_prefix(self):
        c=NativeProofCoverage()
        for i in range(65535):self.assertEqual(c.append([]),i)
        self.assertEqual(c.append([(65534,'completed'),(0,'applied')]),65535)
        self.assertTrue(c.covers(65535,65534,'completed'));self.assertTrue(c.covers(65535,0,'applied'))
        self.assertFalse(c.covers(65535,0,'completed'));self.assertFalse(c.covers(65535,65535,'applied'))
        with self.assertRaisesRegex(ValueError,'identity limit'):c.append([])
        self.assertEqual(len(c),65536)

    def test_controller_counters_match_successful_insertions_and_rejections(self):
        c=ModernNativeController(topology(),protocol(),example())
        for kind,limit in (('remote',1024),('local',256)):
            for _ in range(limit):c._schedule(dict(kind=kind),dict(role='unit_inert'),[])
            size=len(c.native_nodes)
            with self.assertRaisesRegex(ValueError,kind+' operation limit'):
                c._schedule(dict(kind=kind),dict(role='unit_inert'),[])
            self.assertEqual(len(c.coverage),size);self.assertEqual(len(c.tasks),size)
        self.assertEqual((c._remote_count,c._local_count),(1024,256))
        self.assertEqual((len(c.native_nodes),len(c.coverage)),(1280,1280))

    def test_unknown_kind_or_wide_or_future_proof_does_not_mutate_controller(self):
        c=ModernNativeController(topology(),protocol(),example())
        for _ in range(33):c._schedule(dict(kind='remote'),dict(role='unit_inert'),[])
        for node,proof in ((dict(kind='bad'),[]),(dict(kind='local'),[(i,'applied') for i in range(33)]),
                           (dict(kind='local'),[(33,'completed')])):
            with self.assertRaises(ValueError):c._schedule(node,dict(role='unit_inert'),proof)
            self.assertEqual((c._remote_count,c._local_count,len(c.coverage),len(c.native_nodes)),(33,0,33,33))


if __name__=='__main__':unittest.main()
