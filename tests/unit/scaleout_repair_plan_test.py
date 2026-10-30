"""Loop safety, old ECMP splits and explicit repair planning, without network runs."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.scaleout_repair_plan import RoutingGraph, execute, plan
from moe_ub_cosim.scaleout_topology import generate

ROOT = Path(__file__).resolve().parents[2]


def graph(edges, gpus=1):
    nodes = [dict(id=i, kind='gpu' if i < gpus else 'agg') for i in range(max(max(e) for e in edges)+1)]
    ports = [0]*len(nodes)
    links = []
    for a, b in edges:
        ports[a] += 1; ports[b] += 1
        links.append(dict(src=a, dst=b, src_port=ports[a], dst_port=ports[b]))
    return RoutingGraph(dict(nodes=nodes, links=links))


class RepairPlanTest(unittest.TestCase):
    def test_strict_LFA_uses_old_graph_not_failed_graph(self):
        g = graph([(0, 2), (1, 2), (1, 3), (3, 4), (2, 4)])
        row = g.plan_destination(1, 0, (1, 2))
        self.assertEqual(row['state'], 'explicit_repair_required')
        candidate = row['LFA_candidates'][0]
        self.assertEqual(candidate['neighbor_to_destination_hops'], candidate['via_source_hops'])
        self.assertFalse(candidate['loop_free'])
        # Neighbor3 has both a safe and an unsafe equal-cost suffix. It cannot
        # be the merge point merely because one of those suffixes is safe.
        self.assertEqual(row['repair_prefix_nodes'], [1, 3, 4])
        self.assertEqual(row['merge'], 4)
        self.assertEqual({(e['src'], e['dst']) for e in row['all_old_suffix_edges']}, {(4, 2), (2, 0)})

    def test_ordinary_LFA_when_all_old_paths_avoid_source(self):
        row = graph([(0, 2), (1, 2), (1, 3), (2, 3)]).plan_destination(1, 0, (1, 2))
        self.assertEqual(row['state'], 'ordinary_LFA_available')
        self.assertEqual([n['peer'] for n in row['LFA_next_hops']], [3])

    def test_surviving_primary_preserves_old_order(self):
        row = graph([(0, 4), (1, 3), (3, 4), (1, 2), (2, 4)]).plan_destination(1, 0, (1, 2))
        self.assertEqual(row['state'], 'primary_survives')
        self.assertEqual([n['peer'] for n in row['primary_next_hops']], [3, 2])
        self.assertEqual([n['peer'] for n in row['surviving_primary_next_hops']], [3])

    def test_partition_is_not_fabricated_repair(self):
        row = graph([(0, 2), (1, 2), (1, 3)]).plan_destination(1, 0, (1, 2))
        self.assertEqual(row['state'], 'partitioned')
        self.assertEqual(row['failed_graph_hops'], -1)

    def test_GPU_is_never_transit(self):
        g = graph([(0, 2), (1, 2), (1, 3), (3, 4)], gpus=2)
        self.assertEqual(g.distances(0)[4], -1)
        self.assertEqual(g.plan_destination(4, 0, (3, 4))['state'], 'originally_unreachable')

    def topology(self):
        return generate(json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes()))[0]

    def test_128Clos_coverage_and_merge(self):
        topology = self.topology()
        result, summary = plan(topology, 128, 132)
        self.assertEqual(summary['endpoint_destination_pairs'], 256)
        self.assertEqual(summary['coverage'], dict(explicit_repair_required=16, primary_survives=112, unaffected=128))
        row = next(r for r in result['destinations'] if r['source'] == 132 and r['destination'] == 1)
        self.assertEqual(row['repair_prefix_nodes'], [132, 129, 133])
        self.assertEqual(row['failed_graph_path'], [132, 129, 133, 128, 1])
        self.assertTrue(all(not r['loop_free'] for r in row['LFA_candidates']))

    def test_invalid_topology_and_edges_fail_before_output(self):
        good = self.topology()
        changed = []
        t = deepcopy(good); t['nodes'][132]['plane'] = 1; changed.append(t)
        t = deepcopy(good); t['links'].append(deepcopy(t['links'][0])); changed.append(t)
        t = deepcopy(good); t['links'][0]['src_port'] = True; changed.append(t)
        t = deepcopy(good); t['nodes'][0]['id'] = True; changed.append(t)
        t = deepcopy(good); t['links'][1]['src_port'] = t['links'][0]['src_port']; t['links'][1]['src'] = t['links'][0]['src']; changed.append(t)
        for topology in changed:
            with self.subTest(topology=topology['nodes'][132]), self.assertRaises(ValueError):
                plan(topology, 128, 132)
        for a, b in ((True, 132), (128, 128), (132, 128), (0, 132), (128, 144)):
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                plan(good, a, b)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); (p/'topology.json').write_text(json.dumps(changed[0]))
            with self.assertRaises(ValueError): execute(p, 128, 132, p/'out')
            self.assertFalse((p/'out').exists())

    def test_output_is_exclusive_and_inputs_unmodified(self):
        topology = self.topology()
        before = deepcopy(topology)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); (p/'topology.json').write_text(json.dumps(topology))
            summary = execute(p, 128, 132, p/'out')
            self.assertFalse(summary['network_executed'])
            self.assertTrue((p/'out/manifest.json').is_file())
            with self.assertRaises(FileExistsError): execute(p, 128, 132, p/'out')
        self.assertEqual(topology, before)

    def test_scale_plane_and_multi_NIC_boundaries(self):
        c = json.loads((ROOT/'examples/scaleout_clos128/clos-128.json').read_bytes())
        c['deployment']['servers_per_segment'] = 4
        with self.assertRaises(ValueError): plan(generate(c)[0], 64, 68)
        topology = self.topology()
        topology['nodes'][132]['plane'] = False
        with self.assertRaises(ValueError): plan(topology, 128, 132)
        topology = self.topology()
        topology['links'].append(dict(src=0, dst=129, src_port=2, dst_port=1024))
        with self.assertRaises(ValueError): plan(topology, 128, 132)


if __name__ == '__main__':
    unittest.main()
