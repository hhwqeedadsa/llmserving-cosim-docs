"""Compare future-route rejection and counts with the preserved full constructor."""
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import types
import unittest

from moe_ub_cosim.plan import validate_config,build_plan,MAX_TIME_NS
from moe_ub_cosim.resources import actual_usage
from moe_ub_cosim.route_admission import CannRouteAdmission

ROOT=Path(__file__).resolve().parents[2]


def legacy():
    source=subprocess.check_output(['git','show','d38d9e7:python/moe_ub_cosim/cann.py'],cwd=ROOT)
    module=types.ModuleType('moe_ub_cosim._route_admission_baseline');module.__package__='moe_ub_cosim'
    exec(compile(source,'d38d9e7-cann.py','exec'),module.__dict__)
    return module.build_plan


class RouteAdmissionTest(unittest.TestCase):
    def test_old_graph_counts_rejections_and_current_graph_bytes(self):
        old=legacy();rng=random.Random(10)
        layers=json.loads((ROOT/'fixtures/m10_full_model/layers.json').read_text())['groups'][0]['layers']
        checked=0
        for layer in layers:
            for _ in range(12):
                c=deepcopy(layer);c['tokens']=c['tokens'][:1]
                token=c['tokens'][0]
                token.update(src_rank=rng.choice(c['rank_ids']),expert_ids=rng.sample(range(len(c['expert_placement'])),c['top_k']))
                c['chunk_bytes']=rng.choice([257,1024,8192])
                admission=CannRouteAdmission(c);expected=old(c)
                self.assertEqual(admission.check(token),actual_usage(expected))
                self.assertEqual(build_plan(c),expected)
                checked+=1
        self.assertEqual(checked,36)

    def test_malformed_token_and_count_or_duration_overflow_still_reject(self):
        old=legacy();base=json.loads((ROOT/'fixtures/m10_full_model/layers.json').read_text())['groups'][0]['layers'][0]
        changes=[lambda c:c['tokens'][0].update(expert_ids=[False,1]),
            lambda c:c['tokens'][0].update(expert_ids=[0,0]),
            lambda c:c['tokens'][0].update(expert_ids=[999,1]),
            lambda c:c['tokens'][0].update(expert_ids=[0]),
            lambda c:c['tokens'][0].update(src_rank=555),
            lambda c:c['tokens'][0].update(token_index=MAX_TIME_NS),
            lambda c:c['tokens'][0].update(phase='prefill'),
            lambda c:c['tokens'][0].update(request_id=''),
            lambda c:c['cann_profile']['local_cost_ns'].update(status_check=MAX_TIME_NS),
            lambda c:c['cann_profile']['local_cost_ns'].update(dispatch_reorder_per_input=MAX_TIME_NS),
            lambda c:c['compute_profile_ns'].update(combine_unpack_merge=MAX_TIME_NS-10),
            lambda c:(c.update(chunk_bytes=1),c['tokens'][0].update(expert_ids=[4,5]))]
        for change in changes:
            c=deepcopy(base);change(c)
            with self.subTest(change=change):
                with self.assertRaises(ValueError):validate_config(c);old(c)
                with self.assertRaises(ValueError):CannRouteAdmission(c).check(c['tokens'][0])
        c=deepcopy(base);c['resource_budget']=dict(schema_version='moe-ub-graph-resources/1',
            max_graph_transfers=1,max_graph_nodes=10000,max_plan_bytes=2**20,
            max_run_graphs=1,max_run_transfers=1,max_run_nodes=10000)
        with self.assertRaisesRegex(ValueError,'budget'):old(c)
        with self.assertRaisesRegex(ValueError,'budget'):CannRouteAdmission(c).check(c['tokens'][0])


if __name__=='__main__':unittest.main()
