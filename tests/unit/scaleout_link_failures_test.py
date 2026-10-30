"""Whole-schedule preflight: no implicit NIC failure or early convergence."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_link_failures import validate_options, validate_topology
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_rdma import validate_probe

ROOT = Path(__file__).resolve().parents[2]


class LinkFailureTest(unittest.TestCase):
    def setUp(self):
        self.protocol = json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology, _ = generate(json.loads((ROOT/'examples/scaleout_rdma/small-clos-100g.json').read_text()))
        self.edge = next(e for e in self.topology['links'] if e['src'] >= 8)
        self.event = dict(src=self.edge['src'], dst=self.edge['dst'], time_ns=10000, route_update_delay_ns=2000)
        self.protocol['link_failures'] = dict(mode='switch_links/1', events=[self.event])

    def test_public_preflight_and_distinct_detection_times(self):
        second = next(e for e in self.topology['links'] if e['src'] >= 8 and e != self.edge)
        self.protocol['link_failures']['events'].append(dict(src=second['src'], dst=second['dst'],
                                                           time_ns=11000, route_update_delay_ns=100000))
        validate_probe(self.topology, self.protocol, [dict(id=0, src=0, dst=4, plane=0, bytes=1000, ready_ns=0)])

    def test_cutoff_and_zero_delay(self):
        self.event.update(time_ns=4999999, route_update_delay_ns=0)
        validate_topology(self.topology, self.protocol)
        self.event['route_update_delay_ns'] = 1
        with self.assertRaises(ValueError): validate_options(self.protocol)

    def test_malformed_schedules(self):
        for value in [None, {}, dict(mode='bad', events=[self.event]), dict(mode='switch_links/1', events=[]),
                      dict(mode='switch_links/1', events=[self.event]*9),
                      dict(mode='switch_links/1', events=[self.event], extra=1)]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_options(dict(self.protocol, link_failures=value))

    def test_event_types_order_fields_and_duplicate(self):
        for key, value in [('src', True), ('dst', 1.2), ('src', -1), ('dst', 1280),
                           ('dst', self.event['src']), ('time_ns', True), ('time_ns', -1),
                           ('route_update_delay_ns', -1), ('route_update_delay_ns', 5000000), ('extra', 0)]:
            config = deepcopy(self.protocol); config['link_failures']['events'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): validate_options(config)
        self.protocol['link_failures']['events'].append(dict(self.event))
        with self.assertRaisesRegex(ValueError, 'duplicate'): validate_options(self.protocol)

    def test_physical_switch_edge_required(self):
        for pair in [(0, self.topology['links'][0]['dst']), (8, 1279), (8, 9)]:
            self.event.update(src=pair[0], dst=pair[1])
            with self.subTest(pair=pair), self.assertRaisesRegex(ValueError, 'actual switch-switch'):
                validate_topology(self.topology, self.protocol)

    def test_explicit_live_ecmp_is_accepted_by_public_preflight(self):
        self.protocol['local_failover'] = dict(mode='live_ecmp/1')
        validate_probe(self.topology, self.protocol, [dict(id=0, src=0, dst=4, plane=0, bytes=1000, ready_ns=0)])

    def test_local_failover_requires_exact_mode_and_explicit_schedule(self):
        for policy in (None, False, [], {}, dict(mode=None), dict(mode=True), dict(mode='frr'),
                       dict(mode='live_ecmp/1', latency_ns=0)):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                validate_options(dict(self.protocol, local_failover=policy))
        config = dict(self.protocol, local_failover=dict(mode='live_ecmp/1')); del config['link_failures']
        with self.assertRaises(ValueError): validate_options(config)

    def test_public_live_example_is_a_complete_persistent_input(self):
        from moe_ub_cosim.scaleout_streams import validate
        directory = ROOT/'examples/scaleout_live_ecmp'
        topology, _ = generate(json.loads((directory/'topology.json').read_text()))
        for filename in ('work.json', 'work-empty-route.json'):
            with self.subTest(filename=filename):
                validate(topology, json.loads((directory/'protocol.json').read_text()), json.loads((directory/filename).read_text()))

    def test_temporary_reports_may_arrive_in_either_order(self):
        self.protocol['link_failures']['mode'] = 'switch_links/2'
        for down_delay, restore, up_delay in ((2000, 20000, 1000), (30000, 15000, 0),
                                               (20000, 15000, 40000), (0, 10001, 0)):
            self.event.update(route_update_delay_ns=down_delay, restore_time_ns=restore,
                              restore_route_update_delay_ns=up_delay)
            validate_topology(self.topology, self.protocol)

    def test_temporary_schedule_types_bounds_and_versions(self):
        self.protocol['link_failures']['mode'] = 'switch_links/2'
        self.event.update(restore_time_ns=20000, restore_route_update_delay_ns=2000)
        invalid = [('restore_time_ns', True), ('restore_time_ns', 1.5), ('restore_time_ns', -1),
                   ('restore_time_ns', 10000), ('restore_time_ns', 9999), ('restore_time_ns', 5000000),
                   ('restore_route_update_delay_ns', True), ('restore_route_update_delay_ns', -1),
                   ('restore_route_update_delay_ns', 4980000), ('extra', 1)]
        for key, value in invalid:
            c = deepcopy(self.protocol)
            c['link_failures']['events'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_options(c)
        for missing in ('restore_time_ns', 'restore_route_update_delay_ns'):
            c = deepcopy(self.protocol)
            del c['link_failures']['events'][0][missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                validate_options(c)
        self.protocol['link_failures']['mode'] = 'switch_links/1'
        with self.assertRaises(ValueError):
            validate_options(self.protocol)

    def test_temporary_example_prepares_independent_clos128_inputs(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('restore_example', ROOT/'examples/scaleout_link_restore/prepare.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        topology_config, protocols, work = module.inputs()
        topology, _ = generate(topology_config)
        from moe_ub_cosim.scaleout_streams import validate
        self.assertEqual(sum(n['kind'] == 'gpu' for n in topology['nodes']), 128)
        for protocol in protocols.values():
            validate(topology, protocol, work)


if __name__ == '__main__': unittest.main()
