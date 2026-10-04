"""Measurement hooks must not replace or alter the production full-model DAG."""
import json
from pathlib import Path
import unittest

from moe_ub_cosim import full_model, multilayer
from moe_ub_cosim.full_round_probe import instrument
from moe_ub_cosim.plan import RunIdentities


class FullRoundProbeTest(unittest.TestCase):
    def test_instrumentation_preserves_plan_and_restores_functions(self):
        root=Path(__file__).resolve().parents[2]
        source=json.loads((root/'fixtures/m10_full_model/service.json').read_text())['groups'][0]
        configs=json.loads((root/'fixtures/m10_full_model/layers.json').read_text())['groups'][0]['layers']
        def build():
            return full_model.build_full_model(configs,RunIdentities(),[[] for _ in configs],
                source['model_spec'],source['full_model_profile'])
        original=(multilayer.build_plan,full_model.compose_full_model)
        expected=build(); events=[]
        with instrument(lambda name,**row:events.append(dict(phase=name,**row))):
            actual=build()
        self.assertEqual(expected,actual)
        self.assertEqual(original,(multilayer.build_plan,full_model.compose_full_model))
        self.assertEqual([e['layer_id'] for e in events if e['phase']=='layer_end'],[1,2,3])
        self.assertEqual([e['call'] for e in events if e['phase']=='composition_end'],[1,2])

    def test_production_error_propagates_and_hooks_restore(self):
        original=(multilayer.build_plan,full_model.compose_full_model)
        with self.assertRaises(ValueError), instrument(lambda *args,**kwargs:None):
            full_model.compose_full_model([],{}, {})
        self.assertEqual(original,(multilayer.build_plan,full_model.compose_full_model))


if __name__=='__main__':unittest.main()
