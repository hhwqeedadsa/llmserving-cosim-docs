"""Boundary checks for functional addressed native transactions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate
ROOT = Path(__file__).resolve().parents[2]


class TransactionInputTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_text())
        self.protocol = json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology = dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i, kind='gpu') for i in range(8)]+[dict(id=8, kind='tor', plane=0)],
            links=[dict(src=i, dst=8, src_port=1, dst_port=i+1, rate_bps=100000000000) for i in range(8)])

    def test_legal_and_non_power_two_depth(self):
        validate(self.topology, self.protocol, self.config)
        c = deepcopy(self.config); c['connections'][0]['queue_depth'] = 3
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_remote_authority_bounds_permissions(self):
        for key, value in [('key', 8), ('address', 4097), ('value', -1), ('value', True), ('value', 2**64)]:
            c = deepcopy(self.config); c['operations'][1][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['windows'][0]['allow_atomic'] = False
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['address'] = 4150
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_future_unknown_events_and_no_guessed_time(self):
        for after in ([dict(operation=5, event='completed')], [dict(operation=0, event='visible')]):
            c = deepcopy(self.config); c['operations'][1]['after'] = after
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['ready_ns'] = 0
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_protocol_is_explicit_not_legacy_or_overlapping_unordered_puts(self):
        c = deepcopy(self.config); c['operations'][3]['address'] = c['operations'][0]['address']; c['operations'][3]['after'] = []
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['windows'].append(deepcopy(c['windows'][0]))
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['connections'][0]['response_port'] = c['connections'][0]['request_port']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        for key in ('recovery', 'faults', 'admission'):
            protocol = deepcopy(self.protocol); protocol[key] = {}
            with self.assertRaises(ValueError): validate(self.topology, protocol, self.config)


if __name__ == '__main__': unittest.main()
