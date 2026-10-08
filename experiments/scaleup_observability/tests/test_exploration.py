"""Independent factor, graph-control and signed comparison checks."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import yaml

from experiments.scaleup_observability.common import read
from experiments.scaleup_observability.exploration import prepare, scaled, tempo_inputs
from experiments.scaleup_observability.exploration_analysis import pair_metrics, tempo_graph_signature, segment_audit
from experiments.scaleup_observability.inputs import make_input, verified_case
from experiments.scaleup_observability.slots import verify_pair_inputs


class Tempo(unittest.TestCase):
    def test_factors_change_only_selected_costs_and_preserve_inputs(self):
        service,layers,_=make_input(4,8)
        old=deepcopy((service,layers))
        s,l=tempo_inputs(service,layers,1,2)
        self.assertEqual((service,layers),old)
        self.assertEqual(s['requests'],service['requests'])
        self.assertEqual(s['groups'][0]['model_spec'],service['groups'][0]['model_spec'])
        profile=s['groups'][0]['full_model_profile']
        self.assertEqual(profile['layers'][0],service['groups'][0]['full_model_profile']['layers'][0])
        for cost in profile['layers'][1:]:
            self.assertEqual(cost['routed_expert_per_input_ns'],50)
            self.assertEqual(cost['per_token_ns']['shared_expert'],250)
            self.assertEqual(cost['per_token_ns']['dispatch_quant_pack'],20)
            self.assertEqual(cost['per_token_ns']['combine_unpack_merge'],100)
        self.assertEqual(l['groups'][0]['layers'][0]['compute_profile_ns']['expert_per_input'],50)
        self.assertEqual(tempo_inputs(s,l,2,1),(service,layers))
        with self.assertRaisesRegex(ValueError,'exact integer'): scaled(3,1,2)

    def test_fresh_batch_and_paired_input_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'batch'; prepare('compute-tempo',root)
            self.assertEqual(len(read(root/'batch.json')['cases']),6)
            for factor in ('half','base','double'):
                _,a=verified_case(root,f's4-b8-{factor}-slots1')
                _,b=verified_case(root,f's4-b8-{factor}-slots2')
                verify_pair_inputs(a,b)
            _,base=verified_case(root,'s4-b8-base-slots1')
            self.assertEqual(read(base/'service.json'),make_input(4,8)[0])
            with self.assertRaisesRegex(ValueError,'exists'): prepare('compute-tempo',root)

    def test_graph_normalization_does_not_hide_dependency_or_unselected_compute_change(self):
        a=dict(model_spec={},operations=[],transfers=[{'bytes':64}],expert_input_counts=[3],
            rank_graphs=[dict(nodes=[dict(kind='COMP',name='expert-compute',duration_ns=300,deps=[]),
                                    dict(kind='COMP',name='pack',duration_ns=20,deps=[1])],model_stages=[])],
            full_model_profile=dict(layers=[dict(routed_expert_per_input_ns=100,per_token_ns=dict(shared_expert=500))]))
        b=deepcopy(a);b['rank_graphs'][0]['nodes'][0]['duration_ns']=150
        cost=b['full_model_profile']['layers'][0];cost['routed_expert_per_input_ns']=50;cost['per_token_ns']['shared_expert']=250
        self.assertEqual(tempo_graph_signature(a,1,1),tempo_graph_signature(b,1,2))
        b['rank_graphs'][0]['nodes'][1]['deps']=[]
        self.assertNotEqual(tempo_graph_signature(a,1,1),tempo_graph_signature(b,1,2))
        b['rank_graphs'][0]['nodes'][1].update(deps=[1],duration_ns=10)
        self.assertNotEqual(tempo_graph_signature(a,1,1),tempo_graph_signature(b,1,2))

    def test_signed_metrics_keep_regressions(self):
        def row(name,time,wait,queue):
            return dict(case=dict(case_id=name),stats=dict(round_ns=time,full_slot_wait_rank_ns=wait,
                network=dict(summed_voq_byte_ns=queue,summed_fifo_byte_ns=0)))
        r=pair_metrics(row('a',100,30,10),row('b',120,15,25))
        self.assertEqual(r['round_reduction_fraction'],-.2)
        self.assertEqual(r['full_slot_wait_delta_rank_ns'],-15)
        self.assertEqual(r['queue_area_delta_byte_ns'],15)


class LoadGrain(unittest.TestCase):
    def test_only_response_size_changes_across_grains(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'batch';prepare('load-grain',root)
            first=None
            for size in (128,512,2048):
                _,a=verified_case(root,f's4-b8-load{size}-slots1')
                _,b=verified_case(root,f's4-b8-load{size}-slots2')
                verify_pair_inputs(a,b)
                scenario=yaml.safe_load((a/'scenario.yaml').read_text())
                self.assertEqual(scenario['backend']['attributes'].pop('ns3::UbLdstThread::LoadResponseSize'),size)
                content={p.name:p.read_bytes() for p in a.iterdir() if p.name!='scenario.yaml'}
                if first is None:first=(scenario,content)
                self.assertEqual((scenario,content),first)
                self.assertEqual(read(a/'service.json'),make_input(4,8)[0])

    def test_actual_response_count_and_store_tail(self):
        rows=[dict(operation='LOAD',payload_bytes=2048,committed_segments=16),
              dict(operation='STORE',payload_bytes=1068,committed_segments=3),
              dict(operation='STORE',payload_bytes=32,committed_segments=1)]
        counts=segment_audit(rows,128,512)
        self.assertEqual(counts['LOAD'],dict(tasks=1,committed_segments=16,single_segment_tasks=0))
        self.assertEqual(counts['STORE'],dict(tasks=2,committed_segments=4,single_segment_tasks=1))
        rows[0]['committed_segments']=1
        self.assertEqual(segment_audit(rows,2048,512)['LOAD']['single_segment_tasks'],1)
        with self.assertRaisesRegex(ValueError,'segmentation differs'):segment_audit(rows,512,512)


if __name__=='__main__': unittest.main()
