from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.operator_profile import evaluate,validate_profile
from moe_ub_cosim.plan import build_plan,validate_plan
from moe_ub_cosim.evidence_service import EvidenceService

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    layer=json.loads((ROOT/'fixtures/m8_composed_service/layers.json').read_text())['groups'][0]['layers'][0]
    layer['operator_profile']=json.loads((ROOT/'fixtures/m9_evidence/profile-test.json').read_text())
    return layer


class OperatorProfileTest(unittest.TestCase):
    def test_equal_total_expert_work_has_distinct_lookup(self):
        c=fixture();c['expert_placement']=[2,2,7,12]
        base=c['tokens'][0];base.update(expert_ids=[0,2],src_rank=12)
        c['tokens']=[dict(base,request_id=str(i),src_rank=r) for i,r in enumerate([2,2,7,7])]
        # The two local experts have 4/0 vs 2/2 inputs. Same total is not the key.
        cold=evaluate(c)['ranks'][0]['operators']['expert_ffn']
        c['tokens'][2]['expert_ids']=c['tokens'][3]['expert_ids']=[1,2]
        spread=evaluate(c)['ranks'][0]['operators']['expert_ffn']
        self.assertEqual(cold['shape'],[4,0]);self.assertEqual(spread['shape'],[2,2])
        self.assertEqual(cold['duration_ns'],1400);self.assertEqual(spread['duration_ns'],2400)

    def test_context_lookup_and_zero_work(self):
        c=fixture();c['tokens'][0].update(token_index=2,expert_ids=[0,1])
        first=evaluate(c);c['tokens'][0]['token_index']=8;second=evaluate(c)
        self.assertEqual(first['ranks'][0]['operators']['attention_decode']['duration_ns'],1030)
        self.assertEqual(second['ranks'][0]['operators']['attention_decode']['duration_ns'],1090)
        self.assertEqual(second['ranks'][2]['operators']['expert_ffn']['duration_ns'],0)
        self.assertIsNone(second['ranks'][2]['operators']['attention_decode']['row_index'])

    def test_exact_domain_statistic_and_shape_tampering(self):
        for mutation in (
            lambda c:c['tokens'][0].update(token_index=10000),
            lambda c:c['operator_profile']['domain'].update(dtype='bfloat16'),
            lambda c:c['operator_profile']['domain'].update(tp=True),
            lambda c:c['operator_profile']['measurement'].update(out_of_domain='linear'),
            lambda c:c['operator_profile']['rows'].append(deepcopy(c['operator_profile']['rows'][0])),
            lambda c:c['operator_profile']['rows'][0].update(duration_ns=1)):
            c=fixture();mutation(c)
            with self.assertRaises(ValueError):evaluate(c)

    def test_actual_graph_times_and_independent_validation(self):
        c=fixture();c['tokens'][0]['expert_ids']=[0,1]
        plan=build_plan(c)
        row=plan['rank_graphs'][0];nodes={n['node_id']:n for n in row['nodes']}
        self.assertEqual(nodes[row['phase_nodes']['prepare']]['duration_ns'],1230)
        self.assertEqual(nodes[row['phase_nodes']['expert']]['duration_ns'],1100)
        bad=deepcopy(plan);bad['compute_evaluation']['ranks'][0]['operators']['expert_ffn']['duration_ns']+=1
        with self.assertRaisesRegex(ValueError,'evaluation mismatch'):validate_plan(bad)
        bad=deepcopy(plan);bad['rank_graphs'][0]['nodes'][0]['duration_ns']+=1
        with self.assertRaisesRegex(ValueError,'Canonical plan'):validate_plan(bad)

    def test_profile_references_match_model_inventory(self):
        c=json.loads((ROOT/'fixtures/m9_evidence/profiled-service.json').read_text())
        l=json.loads((ROOT/'fixtures/m8_composed_service/layers.json').read_text())
        service=EvidenceService(c,l)
        self.assertEqual(service.evidence['compute_source'],'operator_profiles_with_reference_residual')
        self.assertTrue(all(g['compute_source']=='operator_profiles_with_reference_residual'
                            for g in service.evidence['groups'].values()))
        self.assertEqual(service.manifest_fields()['admission_policy'],'fcfs_observation_input_bound_reservation')
        wrong=deepcopy(c);wrong['service']['groups'][0]['model_spec']['dtypes']['weights']='bfloat16'
        with self.assertRaisesRegex(ValueError,'weight dtype'):EvidenceService(wrong,l)
        c['service']['groups'][0]['model_spec']['layers'][1]['intermediate_size']=128
        with self.assertRaisesRegex(ValueError,'model structure'):EvidenceService(c,l)


if __name__=='__main__':unittest.main()
