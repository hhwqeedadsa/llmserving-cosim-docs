"""Buffer conservation differs from drain and excludes priority-zero controls."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.scaleout_mmu import analyze
from moe_ub_cosim.scaleout_rdma import validate_probe, validate_protocol
from moe_ub_cosim.scaleout_topology import generate

ROOT = Path(__file__).resolve().parents[2]


def state():
    ports = [dict(port=p, link_up=True, **{k: [0]*8 for k in
        ('ingress_bytes', 'headroom_used_bytes', 'egress_mmu_bytes', 'queue_bytes', 'pfc_paused')}) for p in (1, 2)]
    ports[0]['ingress_bytes'][3] = 10000
    ports[0]['headroom_used_bytes'][3] = 500
    ports[1]['egress_mmu_bytes'][3] = ports[1]['queue_bytes'][3] = 10500
    ports[1]['queue_bytes'][0] = 96
    return dict(schema_version='native-switch-mmu-snapshot/1', time_ns=4000,
        selected_nodes=[8], complete_selected_switch_ports=True,
        switches=[dict(node=8, physical_port_count=2, reserve_bytes=4096, shared_used_bytes=5904, ports=ports)])


class MmuTest(unittest.TestCase):
    def setUp(self):
        ex = ROOT/'examples/scaleout_rdma'
        self.protocol = json.loads((ex/'rdma.json').read_text())
        self.topology, _ = generate(json.loads((ex/'small-clos-100g.json').read_text()))
        self.message = [dict(id=0, src=0, dst=4, bytes=1000, plane=0, ready_ns=0)]

    def test_selected_all_physical_ports_and_exact_bound(self):
        self.protocol['mmu_snapshot'] = dict(nodes=[8], max_ports=1024)
        validate_probe(self.topology, self.protocol, self.message)
        count = sum(l[side] == 8 for l in self.topology['links'] for side in ('src', 'dst'))
        self.protocol['mmu_snapshot']['max_ports'] = count
        validate_probe(self.topology, self.protocol, self.message)
        self.protocol['mmu_snapshot']['max_ports'] = count-1
        with self.assertRaisesRegex(ValueError, 'exceed max_ports'):
            validate_probe(self.topology, self.protocol, self.message)

    def test_bad_selections_rejected(self):
        for option in (None, {}, dict(nodes=[], max_ports=10), dict(nodes=[8, 8], max_ports=10),
                dict(nodes=[True], max_ports=10), dict(nodes=[8.0], max_ports=10),
                dict(nodes=[8], max_ports=True), dict(nodes=[8], max_ports=1025),
                dict(nodes=list(range(8, 25)), max_ports=1024), dict(nodes=[8], max_ports=10, extra=1)):
            with self.subTest(option=option), self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol, mmu_snapshot=option))
        for node in (0, 1279):
            with self.subTest(node=node), self.assertRaisesRegex(ValueError, 'switch present'):
                validate_probe(self.topology, dict(self.protocol, mmu_snapshot=dict(nodes=[node], max_ports=1024)), self.message)

    def test_occupied_headroom_and_control_queue_are_consistent(self):
        report = analyze(state())
        self.assertTrue(report['consistent'])
        self.assertEqual(report['switches'][0]['queued_data_bytes'], 10500)

    def test_cleared_queue_is_detected_even_when_mmu_conserves(self):
        value = state(); value['switches'][0]['ports'][1]['queue_bytes'][3] = 0
        findings = analyze(value)['findings']
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['kind'], 'egress_queue_mismatch')

    def test_ingress_and_shared_discrepancies_are_separate(self):
        value = state(); value['switches'][0]['ports'][0]['ingress_bytes'][3] += 1
        self.assertEqual({f['kind'] for f in analyze(value)['findings']},
            {'ingress_egress_mismatch', 'shared_occupancy_mismatch'})

    def test_incomplete_or_malformed_snapshots_are_rejected(self):
        variants = []
        value=state();value['switches'][0]['ports'].pop();variants.append(value)
        value=state();value['switches'][0]['ports'][0]['queue_bytes'][3]=True;variants.append(value)
        value=state();value['switches'][0]['ports'][0]['pfc_paused'][3]=2;variants.append(value)
        value=state();value['selected_nodes']=[9];variants.append(value)
        value=state();value['complete_selected_switch_ports']=False;variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError): analyze(value)


if __name__ == '__main__':
    unittest.main()
