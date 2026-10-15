"""Physical wiring invariants, independent paths and public CLI export checks."""
from collections import Counter, deque
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from moe_ub_cosim.scaleout_topology import generate, export_text

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'examples/scaleout_topology'


def read(name='small-clos'):
    return json.loads((EXAMPLES / (name + '.json')).read_text())


def path(graph, src, dst):
    """Independent BFS: never traverse an endpoint GPU as a switch."""
    adjacency = {n['id']: [] for n in graph['nodes']}
    for link in graph['links']:
        adjacency[link['src']].append(link['dst'])
        adjacency[link['dst']].append(link['src'])
    queue = deque([[src]])
    seen = {src}
    while queue:
        route = queue.popleft()
        if route[-1] == dst:
            return route
        if route[-1] != src and graph['nodes'][route[-1]]['kind'] == 'gpu':
            continue
        for peer in adjacency[route[-1]]:
            if peer not in seen:
                seen.add(peer)
                queue.append(route + [peer])
    return None


class TopologyTest(unittest.TestCase):
    def test_three_tier_counts_wiring_and_cross_pod_paths(self):
        g, a = generate(read())
        self.assertEqual(a['counts'], dict(gpus=8, servers=4, tor=4, agg=4, core=2, links=20))
        for i, j in combinations(range(8), 2):
            route = path(g, i, j)
            self.assertIsNotNone(route)
            if g['nodes'][i]['pod'] != g['nodes'][j]['pod']:
                self.assertEqual(len(route) - 1, 6)
                self.assertEqual(sum(g['nodes'][k]['kind'] == 'core' for k in route), 1)
        for n in g['nodes']:
            if n['kind'] == 'tor':
                peers = [l['dst'] for l in g['links'] if l['src'] == n['id']]
                self.assertEqual(len(peers), 2)
                self.assertTrue(all(g['nodes'][p]['kind'] == 'agg' for p in peers))
        self.assertTrue(a['all_gpu_pairs_have_packet_switch_path'])
        self.assertFalse(a['network_executed'])

    def test_two_tier_without_agg_and_without_core(self):
        c = read()
        c['fabric'].update(aggs_per_pod_per_group=0, tor_uplinks=2, agg_uplinks=0, core_wiring='full_mesh')
        g, a = generate(c)
        self.assertEqual(a['counts']['agg'], 0)
        self.assertEqual(len(path(g, 0, 4)) - 1, 4)
        self.assertEqual(a['link_tiers'], dict(access=8, tor_core=8))
        c = read()
        c['deployment']['pods'] = 1
        c['fabric'].update(cores_per_group=0, agg_uplinks=0, core_wiring='none')
        g, a = generate(c)
        self.assertTrue(a['all_gpu_pairs_have_packet_switch_path'])
        self.assertEqual(len(path(g, 0, 2)) - 1, 4)
        self.assertNotIn('core', {n['kind'] for n in g['nodes']})

    def test_core_wiring_is_exact(self):
        c = read()
        c['fabric'].update(cores_per_group=4, agg_uplinks=2)
        for wiring, expected in [('grouped', [{0, 1}, {2, 3}]), ('cyclic', [{0, 1}, {2, 3}]),
                                 ('full_mesh', [set(range(4)), set(range(4))])]:
            c['fabric'].update(core_wiring=wiring, agg_uplinks=4 if wiring == 'full_mesh' else 2)
            g, _ = generate(c)
            for n in g['nodes']:
                if n['kind'] == 'agg':
                    peers = [g['nodes'][l['dst']]['index'] for l in g['links'] if l['src'] == n['id']]
                    self.assertEqual(set(peers), expected[n['index']])
        c['fabric'].update(core_wiring='cyclic', cores_per_group=3, agg_uplinks=2)
        g, _ = generate(c)
        agg = next(n for n in g['nodes'] if n['kind'] == 'agg' and n['index'] == 1)
        self.assertEqual({g['nodes'][l['dst']]['index'] for l in g['links'] if l['src'] == agg['id']}, {2, 0})

    def test_rail_planes_and_multinic_are_distinct(self):
        c = read()
        c['fabric'].update(rails=2, planes=2, upper_tier_scope='rail')
        c['nic']['rails_by_gpu_slot'] = [[0], [1]]
        c['links']['access']['rate_bps'] = 200_000_000_000
        g, a = generate(c)
        self.assertFalse(a['all_gpu_pairs_have_packet_switch_path'])
        self.assertEqual(a['unreachable_unordered_gpu_pairs'], 16)
        self.assertIsNone(path(g, 0, 1))
        for l in g['links']:
            left, right = g['nodes'][l['src']], g['nodes'][l['dst']]
            if left['kind'] != 'gpu':
                self.assertEqual((left['plane'], left['rail']), (right['plane'], right['rail']))
        c['fabric']['upper_tier_scope'] = 'shared'
        g, a = generate(c)
        self.assertIsNotNone(path(g, 0, 1))
        self.assertTrue(a['all_gpu_pairs_have_packet_switch_path'])
        c['fabric']['upper_tier_scope'] = 'rail'
        c['nic']['rails_by_gpu_slot'] = [[0, 1], [0, 1]]
        g, a = generate(c)
        self.assertTrue(a['all_gpu_pairs_have_packet_switch_path'])
        self.assertEqual(len(g['nic_ports']), 8 * 2 * 2)
        self.assertEqual(a['per_nic']['total_port_rate_bps'], 400_000_000_000)

    def test_no_gpu_transit_when_components_share_a_gpu(self):
        c = read()
        c['fabric'].update(rails=2, upper_tier_scope='rail')
        c['deployment']['gpus_per_server'] = 3
        c['nic']['rails_by_gpu_slot'] = [[0], [0, 1], [1]]
        g, a = generate(c)
        self.assertIsNone(path(g, 0, 2))
        self.assertIsNotNone(path(g, 0, 1))
        self.assertIsNotNone(path(g, 1, 2))
        self.assertFalse(a['all_gpu_pairs_have_packet_switch_path'])
        # Four rail-0-only and four rail-1-only endpoints lack direct paths.
        self.assertEqual(a['unreachable_unordered_gpu_pairs'], 16)

    def test_ports_rates_capacity_and_server_numbering(self):
        c = read()
        c['links']['tor_agg'].update(rate_bps=100_000_000_000, delay_ns=777)
        g, a = generate(c)
        ports, pairs = set(), set()
        for l in g['links']:
            pair = tuple(sorted((l['src'], l['dst'])))
            self.assertNotIn(pair, pairs)
            pairs.add(pair)
            for key in ('src', 'dst'):
                port = (l[key], l[key + '_port'])
                self.assertNotIn(port, ports)
                ports.add(port)
            if l['tier'] == 'tor_agg':
                self.assertEqual((l['rate_bps'], l['delay_ns']), (100_000_000_000, 777))
        for row in a['switch_ports']:
            if row['kind'] == 'tor':
                self.assertEqual(row['down_to_up_ratio'], 4)
        for gpu in g['nodes'][:8]:
            self.assertEqual(gpu['server'], gpu['id'] // 2)
        for node in g['nodes']:
            actual = sorted(p for n, p in ports if n == node['id'])
            self.assertEqual(actual, list(range(1, len(actual) + 1)))

    def test_native_export_headers_and_links(self):
        c = read()
        g, _ = generate(c)
        for simai in (False, True):
            lines = export_text(g, c, simai).splitlines()
            header = lines[0].split()
            self.assertEqual(header, ['18', '2', '0', '10', '20', 'A100'] if simai else ['18', '10', '20'])
            self.assertEqual(set(map(int, lines[1].split())), set(range(8, 18)))
            parsed = []
            for line in lines[2:]:
                src, dst, rate, delay, loss = line.split()
                self.assertEqual(loss, '0')
                parsed.append((int(src), int(dst), int(rate.removesuffix('bps')), int(delay.removesuffix('ns'))))
            self.assertEqual(parsed, [(l['src'], l['dst'], l['rate_bps'], l['delay_ns']) for l in g['links']])

    def test_bad_config_is_rejected(self):
        changes = [('fabric', 'planes', 2), ('fabric', 'cores_per_group', 3),
                   ('fabric', 'aggs_per_pod_per_group', -1), ('port_limits', 'tor', 2),
                   ('budget', 'max_links', 19), ('budget', 'max_nodes', 17), ('budget', 'max_switches', 9),
                   ('deployment', 'pods', True), ('deployment', 'gpu_type', 'RTX3090'),
                   ('nic', 'rails_by_gpu_slot', [[0, 0], [0]]), ('fabric', 'upper_tier_scope', 'guess'),
                   ('links', 'unknown', {})]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                c = read(); c[section][key] = value
                with self.assertRaises(ValueError):
                    generate(c)

    def test_thousand_gpu_examples_counts_and_reachability(self):
        expected = {
            '1024-clos': (128, 128, 64, 3072, True),
            '1024-leaf-spine': (32, 0, 32, 2048, True),
            '1024-hpn-inspired': (64, 32, 8, 2688, True),
            '1024-multirail': (32, 64, 32, 1408, False),
        }
        for name, values in expected.items():
            with self.subTest(name=name):
                g, a = generate(read(name))
                n = a['counts']
                self.assertEqual(n['gpus'], 1024)
                self.assertEqual((n['tor'], n['agg'], n['core'], n['links'], a['all_gpu_pairs_have_packet_switch_path']), values)
                self.assertEqual(Counter(n['kind'] for n in g['nodes'])['gpu'], 1024)

    def test_cli_end_to_end_determinism_and_output_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'python'))
            def invoke(out, config=EXAMPLES / 'small-clos.json'):
                return subprocess.run([sys.executable, '-m', 'moe_ub_cosim.cli', 'scaleout', 'topology',
                    '--config', str(config), '--output', str(out)], cwd=tmp, env=env, capture_output=True, text=True)
            first, second = Path(tmp) / 'first', Path(tmp) / 'second'
            self.assertEqual(invoke(first).returncode, 0)
            self.assertEqual(invoke(second).returncode, 0)
            for f in first.iterdir():
                self.assertEqual(f.read_bytes(), (second / f.name).read_bytes())
            manifest = json.loads((first / 'manifest.json').read_text())
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256((first / name).read_bytes()).hexdigest(), digest)
            self.assertNotEqual(invoke(first).returncode, 0)
            bad = Path(tmp) / 'bad.json'
            c = read(); c['nic']['total_rate_bps'] = 1
            bad.write_text(json.dumps(c))
            self.assertNotEqual(invoke(Path(tmp) / 'invalid', bad).returncode, 0)
            self.assertFalse((Path(tmp) / 'invalid').exists())


if __name__ == '__main__':
    unittest.main()
