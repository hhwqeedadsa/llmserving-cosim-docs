"""Compare static counts with a closed functional controller, never a network."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from modern_native_test import example, expert_matrix, protocol, topology, declared_callback_drive
from moe_ub_cosim.modern_native_capacity import requirements, execute, connection_keys
from moe_ub_cosim.modern_native import run
from moe_ub_cosim.modern_native_layout import ModernNativeLayout
from moe_ub_cosim.modern_deepep_plan import compile_plan


def larger(top_k=2, calls=2):
    cfg=example()
    m=cfg['modern']
    m['model'].update(experts=4,top_k=top_k)
    m['execution'].update(ranks=4,max_tokens_per_rank=16,connections=1,local_devices=[[0] for _ in range(4)])
    m['calls']=[dict(name=f'call-{i}',tokens=[[list(range(top_k)) for _ in range(16)] for _ in range(4)])
                for i in range(calls)]
    m['limits'].update(max_operations=16384,max_events=65536,max_output_bytes=16777216)
    cfg['deployment']=[dict(rank=r,node=r,devices=[dict(device=0,nic=1,plane=0)]) for r in range(4)]
    return cfg


class ModernNativeCapacityTests(unittest.TestCase):
    def test_counts_match_all_actual_functional_submissions_and_source_segments(self):
        for cfg in (example(),expert_matrix()):
            with self.subTest(schema=cfg['schema_version']):
                report=requirements(cfg['modern'])
                c=declared_callback_drive(cfg)
                actual=Counter(n['kind'] for n in c.native_nodes)
                self.assertEqual(actual,{'remote':report['counts']['native_nodes']['remote'],
                                        'local':report['counts']['native_nodes']['local']})
                self.assertTrue(report['passed'])
                self.assertFalse(report['runtime_admitted'])
                self.assertFalse(report['network_executed'])
                for row in report['counts']['immutable_operands']:
                    rank=row['rank']
                    self.assertEqual(row['logical_window_bytes'],c.layout.lengths[rank])
                    self.assertEqual(row['initialized_segments'],len(c.layout.segments[rank]))
                    self.assertEqual(row['initialized_bytes'],sum(len(x['hex'])//2 for x in c.layout.segments[rank]))

    def test_zero_token_still_has_all_self_control_and_count_preparations(self):
        cfg=example()['modern']
        for call in cfg['calls']:call['tokens']=[[],[]]
        r=requirements(cfg)
        # Two ranks, two calls: four barriers each has 2x2 self/remote
        # signals, plus 2x2 expert blocks. Rank counts use local bypass.
        self.assertEqual(r['counts']['native_nodes']['remote'],2*(4*4+4+2))
        self.assertEqual(r['counts']['native_nodes']['count_preparations'],4)
        self.assertGreater(r['counts']['self_connections'],0)
        self.assertEqual([x['initialized_segments'] for x in r['counts']['immutable_operands']],[2,2])

    def test_all_possible_combine_slots_are_retained_without_resolving_one(self):
        cfg=larger(calls=1)['modern']
        cfg['execution'].update(num_sms=2,call_contexts=5,requested_contexts=5,connections=1)
        plan=compile_plan(cfg)
        before=deepcopy(plan)
        keys=connection_keys(plan,cfg['execution'])
        # Sixteen tokens from src0 reach owner1. With two SMs their
        # possible return slots span both contexts 0 and 1.
        self.assertIn((1,0,0),keys)
        self.assertIn((1,1,0),keys)
        self.assertEqual(plan,before)

    def test_complete_remote_and_local_overflows_are_detected_before_backend_or_output(self):
        for cfg,kind in ((larger(),'local'),(larger(top_k=4,calls=4),'remote')):
            report=requirements(cfg['modern'])
            self.assertIn(kind+'_nodes',report['failures'])
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);top=root/'topology';top.mkdir()
                (top/'topology.json').write_text(json.dumps(topology()))
                (root/'rdma.json').write_text(json.dumps(protocol()))
                (root/'input.json').write_text(json.dumps(cfg))
                with patch('moe_ub_cosim.modern_native.monitored') as launch:
                    with self.assertRaisesRegex(ValueError,'complete modern workload requires.*'+kind):
                        run(top,root/'rdma.json',root/'input.json',root/'missing-backend',root/'output')
                    launch.assert_not_called()
                self.assertFalse((root/'output').exists())

    def test_multiple_static_failures_are_saved_without_claiming_full_admission(self):
        cfg=larger(top_k=4,calls=4)['modern']
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'input.json';out=Path(d)/'out.json';src.write_text(json.dumps(cfg))
            r=execute(src,out)
            self.assertFalse(r['passed']);self.assertFalse(r['runtime_admitted'])
            self.assertEqual(json.loads(out.read_text()),r)
            self.assertIn('remote_nodes',r['failures']);self.assertIn('local_nodes',r['failures'])
            self.assertTrue(any('dynamic proof' in s for s in r['unverified']))
            self.assertEqual(next(c for c in r['checks'] if c['name']=='registrations_minimum')['requirement_kind'],'lower_bound')
            old=out.read_bytes()
            with self.assertRaisesRegex(ValueError,'already exists'):execute(src,out)
            self.assertEqual(out.read_bytes(),old)

    def test_invalid_input_and_caller_mutation_do_not_change_a_saved_report(self):
        cfg=example()['modern'];old=deepcopy(cfg)
        r=requirements(cfg);self.assertEqual(cfg,old)
        r['counts']['native_nodes']['paths'].clear();self.assertEqual(requirements(cfg)['counts']['native_nodes']['paths'],
            requirements(old)['counts']['native_nodes']['paths'])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'bad.json';src.write_text('[]')
            with self.assertRaises((ValueError,TypeError)):execute(src,root/'bad-report.json')
            self.assertFalse((root/'bad-report.json').exists())


if __name__=='__main__':unittest.main()
