"""Independent graph/path and resolver checks; no mock network execution."""
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from moe_ub_cosim.scaleout import generate, prepare, remap_layers
from moe_ub_cosim.scenario import read_yaml, resolve_network

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'examples/scaleout/prepare.json'


def config():
    c = json.loads(EXAMPLE.read_text())
    # Exercise same-chip, same-server, same-rack and cross-rack in one graph.
    c['shape']['dies_per_chip'] = 2
    c['groups'] = [dict(group_id='g', rank_ids=[2, 7, 12, 20, 24], rank_to_ub_node=[0, 1, 2, 4, 8])]
    c['budget'] = dict(max_nodes=64, max_links=64, max_route_rows=1000, max_channels=32)
    return c


class ScaleOutTest(unittest.TestCase):
    def test_example_observation_matches_topology_and_window_budget(self):
        from moe_ub_cosim.observation import validate_observation
        c = json.loads(EXAMPLE.read_text())
        observation = json.loads((EXAMPLE.parent / 'observation.json').read_text())
        validate_observation(observation, [2, 7, 12], c['scenario']['max_simulation_ns'])
        nodes = {n['node_id']: n for n in generate(c)[0]['nodes']}
        for sw in observation['switches']:
            self.assertEqual(nodes[sw['switch_id']]['kind'], 'SWITCH')
            self.assertTrue(all(p < nodes[sw['switch_id']]['ports'] for p in sw['port_ids']))

    def test_network_tiers_have_distinct_capacity_and_locality(self):
        c = config()
        baseline = generate(c)
        c['link_profiles']['rack_uplink']['rate_bps'] = 50000000000
        changed = generate(c)
        self.assertEqual(baseline[1:3], changed[1:3])  # Routing/TPs unchanged.
        for before, after in zip(baseline[4], changed[4]):
            if before['scope'] == 'cross_rack':
                self.assertEqual((before['bottleneck_bps'], after['bottleneck_bps']), (100000000000, 50000000000))
            else:
                self.assertEqual(before, after)
        server = next(cut for cut in baseline[5]['cuts'] if cut['level'] == 'server')
        self.assertEqual(server['downstream_bps'], 1600000000000)
        self.assertEqual(server['uplink_bps'], 100000000000)

    def test_routes_are_shortest_reciprocal_and_cover_idle_endpoints(self):
        topology, routes, channels, ports, paths, audit = generate(config())
        adjacent = {}; graph = {}
        for p in ports:
            key = p['node_id'], p['port_id']
            self.assertNotIn(key, adjacent)
            adjacent[key] = (p['peer_node'], p['peer_port'])
            graph.setdefault(p['node_id'], []).append(p['peer_node'])
        self.assertEqual(len(adjacent), sum(n['ports'] for n in topology['nodes']))
        for a, b in adjacent.items():
            self.assertEqual(adjacent[b], a)
        route_map = {(int(r['nodeId']), int(r['dstNodeId'])): r for r in routes}
        devices = audit['counts']['devices']
        # Independent BFS from each destination checks both chosen port and metric.
        for dst in range(devices):
            distance = {dst: 0}; queue = deque([dst])
            while queue:
                node = queue.popleft()
                for neighbor in graph[node]:
                    if neighbor not in distance:
                        distance[neighbor] = distance[node] + 1; queue.append(neighbor)
            self.assertEqual(len(distance), len(topology['nodes']))
            for node in distance:
                if node == dst:
                    continue
                r = route_map[node, dst]
                neighbor, incoming = adjacent[node, int(r['outPorts'])]
                self.assertEqual(distance[neighbor], distance[node] - 1)
                self.assertEqual(int(r['metrics']), distance[node])
                if neighbor == dst:
                    self.assertEqual(incoming, 0)
        transport_ids = set()
        for c in channels:
            for side in ('1', '2'):
                key = c['nodeId' + side], c['tpn' + side]
                self.assertNotIn(key, transport_ids); transport_ids.add(key)
        self.assertEqual(len(channels), 10)
        self.assertEqual(set(p['scope'] for p in paths), {'same_chip', 'same_server', 'same_rack', 'cross_rack'})
        expected_hops = dict(same_chip=0, same_server=2, same_rack=4, cross_rack=6)
        for p in paths:
            self.assertEqual(p['hop_count'], expected_hops[p['scope']])
            self.assertEqual(len(p['egress_ports'].split()), p['hop_count'])
        # Two different source GPUs share the same actual server egress port.
        by_pair = {(p['src_node'], p['dst_node']): p for p in paths}
        self.assertIn('16:4', by_pair[0, 8]['egress_ports'].split())
        self.assertIn('16:4', by_pair[2, 8]['egress_ports'].split())
        self.assertNotIn('16:4', by_pair[0, 2]['egress_ports'].split())

    def test_prepared_tier_rates_survive_existing_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'prepared'
            result = prepare(EXAMPLE, output)
            self.assertEqual(result['state'], 'prepared_not_executed')
            scenario = read_yaml(output / 'scenario.yaml')
            expected = generate(json.loads(EXAMPLE.read_text()))[0]
            _, topology, _, _ = resolve_network(scenario['network'], output, [2, 7, 12], [0, 2, 4], [[0, 2, 4]])
            self.assertEqual(topology['nodes'], expected['nodes'])
            self.assertEqual(sorted(topology['links'], key=lambda l: l['src']), sorted(expected['links'], key=lambda l: l['src']))
            rates = {l['rate_bps'] for l in topology['links']}
            self.assertEqual(rates, {100000000000, 200000000000, 400000000000})
            self.assertEqual(json.loads((output / 'service.json').read_text()),
                             json.loads((ROOT / 'examples/native_first_round/service.json').read_text()))

    def test_remap_changes_only_mapping_and_physical_ownership(self):
        c = json.loads(EXAMPLE.read_text())
        service = json.loads((ROOT / 'examples/native_first_round/service.json').read_text())
        original = json.loads((ROOT / 'examples/native_first_round/layers.json').read_text())
        actual = remap_layers(service, original, c)
        restored = deepcopy(actual)
        for before_g, after_g in zip(original['groups'], restored['groups']):
            for before, after in zip(before_g['layers'], after_g['layers']):
                self.assertEqual(after['rank_to_ub_node'], [0, 2, 4])
                self.assertEqual([r['server_id'] for r in after['deployment']['ranks']], ['server-0', 'server-1', 'server-2'])
                after['rank_to_ub_node'] = before['rank_to_ub_node']
                after['deployment']['ranks'] = before['deployment']['ranks']
        self.assertEqual(restored, original)

    def test_limits_and_invalid_mapping_rejected_before_writing(self):
        bad = []
        for key in ('max_nodes', 'max_links', 'max_route_rows', 'max_channels'):
            c = config(); c['budget'][key] = 1; bad.append(c)
        for key, value in [('racks', True), ('servers_per_rack', 128), ('chips_per_server', 128)]:
            c = config(); c['shape'][key] = value; bad.append(c)
        for key, value in [('rate_bps', 0), ('delay_ns', -1)]:
            c = config(); c['link_profiles']['server_uplink'][key] = value; bad.append(c)
        c = config(); c['groups'][0]['rank_to_ub_node'][0] = 16; bad.append(c)
        c = config(); c['groups'][0]['rank_to_ub_node'][0] = 1; bad.append(c)
        c = config(); c['groups'][0]['rank_ids'][0] = 7; bad.append(c)
        for c in bad:
            with self.assertRaises(ValueError):
                generate(c)
        with tempfile.TemporaryDirectory() as tmp:
            c = json.loads(EXAMPLE.read_text()); c['groups'][0]['rank_ids'][0] = 1
            for k in ('service', 'layer', 'system'):
                c['scenario'][k] = str((EXAMPLE.parent / c['scenario'][k]).resolve())
            c['scenario']['backend']['reference_file'] = str((EXAMPLE.parent / c['scenario']['backend']['reference_file']).resolve())
            path = Path(tmp) / 'bad.json'; path.write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError, 'logical rank'):
                prepare(path, Path(tmp) / 'output')
            self.assertFalse((Path(tmp) / 'output').exists())

    def test_public_cli_reproducible_and_existing_output_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [Path(tmp) / n for n in ('a', 'b')]
            for output in outputs:
                p = subprocess.run([sys.executable, '-m', 'moe_ub_cosim.cli', 'scaleout', 'prepare',
                                    '--config', str(EXAMPLE), '--output', str(output)],
                                   capture_output=True, text=True, timeout=20)
                self.assertEqual(p.returncode, 0, p.stderr)
            for path in outputs[0].rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (outputs[1] / path.relative_to(outputs[0])).read_bytes())
            before = (outputs[0] / 'scaleout-audit.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'already exists'):
                prepare(EXAMPLE, outputs[0])
            self.assertEqual(before, (outputs[0] / 'scaleout-audit.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
