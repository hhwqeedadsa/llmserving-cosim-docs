"""Full service recipes reproduce checked graphs and clean only registered files."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from full_model_test import fixture,ROOT
from moe_ub_cosim import full_model
from moe_ub_cosim.full_model_service import FullModelService,FullModelEvidenceService
from moe_ub_cosim.full_model_recipe import prepare,build_export,identity_state
from moe_ub_cosim.graph_storage import GraphStorage,read_recipe,replay
from moe_ub_cosim.generate import export_plan
from moe_ub_cosim.service import group_layer

LIMITS=dict(schema_version='moe-ub-graph-storage/1',max_recipe_bytes=2**20,
            max_archive_bytes=8*2**20,max_active_graph_bytes=8*2**20)


def service_candidate(evidence=False):
    c,l=fixture()
    if evidence:
        c=json.loads((ROOT/'fixtures/m10_full_model/evidence.json').read_text())
        service=FullModelEvidenceService(c,l)
    else:service=FullModelService(c,l)
    candidate=group_layer(service.layer,service.groups[0]);source=service.segment_inputs[201]
    candidate.update(graph_id='g0',wave_id='D.w0',ready_ns=0,tokens=[dict(request_id='201',src_rank=2,
        phase='decode',token_index=source['prompt_tokens']+source['generated_tokens']-1,
        layer_id=candidate['layer_id'],expert_ids=source['decode_routes'][0][str(candidate['layer_id'])])])
    return service,candidate


class GraphStorageTest(unittest.TestCase):
    def test_input_composition_isolated_equivalence_and_rejection(self):
        c,l=fixture();expected=FullModelService(c,l)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'graphs').mkdir();storage=GraphStorage(out,LIMITS,30)
            actual=FullModelService(c,l,graph_storage=storage)
            self.assertEqual(actual.evidence,expected.evidence)
            self.assertEqual(actual.initial,expected.initial)
            self.assertEqual(actual.deployment(),expected.deployment())
            for group in c['groups']:
                slot=out/'graph-replay/input-checks'/group['group_id']
                self.assertTrue(json.loads((slot/'receipt.json').read_text())['passed'])
                self.assertEqual(json.loads((slot/'command.json').read_text())['exit_code'],0)
            bad=deepcopy(l['groups'][0]['layers']);bad[-1]['tokens'][0]['request_id']='changed'
            with self.assertRaises(ValueError):expected.check_segment(bad,c['groups'][0])
            # Separate owned output keeps the failed worker's diagnostic files.
            failure=out/'bad';failure.mkdir();(failure/'graphs').mkdir()
            broken=GraphStorage(failure,LIMITS,30)
            with self.assertRaisesRegex(ValueError,'Graph builder failed'):
                broken.check_composition(bad,c['groups'][0]['model_spec'],c['groups'][0]['full_model_profile'])
            self.assertFalse((failure/'graph-replay/input-checks'/c['groups'][0]['group_id']/'receipt.json').exists())

    def test_closed_recipe_matches_legacy_full_validation_and_bytes(self):
        for evidence in (False,True):
            with self.subTest(evidence=evidence),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);service,candidate=service_candidate(evidence)
                recipe,consumed,usage=prepare(service,candidate);saved=deepcopy(recipe)
                expected=service.make_plan(candidate);export_plan(expected,out/'old',compact=True)
                with patch.object(full_model,'compose_full_model',wraps=full_model.compose_full_model) as compose:
                    receipt=build_export(recipe,out/'new')
                self.assertEqual(compose.call_count,2)
                self.assertEqual(recipe,saved);self.assertEqual(identity_state(service.identities),receipt['identities'])
                self.assertEqual(json.loads((out/'new/plan.json').read_text()),expected)
                for name in ('plan.json','generation.json','layer.2.et','layer.7.et','layer.12.et'):
                    self.assertEqual((out/'old'/name).read_bytes(),(out/'new'/name).read_bytes())
                bad=deepcopy(recipe);bad['configs'][0]['tokens'][0]['expert_ids']=[999]
                with self.assertRaises(ValueError):build_export(bad,out/'bad')
                self.assertFalse((out/'bad/generation.json').exists())

    def test_worker_retirement_replay_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'graphs').mkdir();service,candidate=service_candidate(True)
            storage=GraphStorage(out,LIMITS,30);directory=out/'graphs/g0'
            storage.generate(service,candidate,directory)
            self.assertEqual(len(service.consumed),3)
            # Never delete a file introduced after generation, or any graph
            # whose own registered input/output changed.
            (directory/'unknown').write_text('keep')
            with self.assertRaisesRegex(ValueError,'changed before cleanup'):storage.retire('g0',1000)
            self.assertTrue((directory/'plan.json').exists());(directory/'unknown').unlink()
            storage.retire('g0',1000);storage.finish()
            self.assertFalse(directory.exists())
            slot=out/'graph-replay/g0';self.assertTrue((slot/'cleanup.json').exists())
            replay(slot/'archive.json',out/'replayed')
            self.assertEqual(json.loads((out/'replayed/generation.json').read_text()),
                             json.loads((slot/'archive.json').read_text())['generation'])
            path=slot/'recipe.json.gz';original=path.read_bytes();path.write_bytes(original[:-2])
            with self.assertRaisesRegex(ValueError,'hash mismatch'):replay(slot/'archive.json',out/'corrupt')
            self.assertFalse((out/'corrupt').exists())
            with self.assertRaises((EOFError,OSError)):read_recipe(path,2**20)

    def test_budget_failure_preserves_diagnostics_and_identity(self):
        for limits in (dict(LIMITS,max_recipe_bytes=1),dict(LIMITS,max_archive_bytes=1),
                       dict(LIMITS,max_active_graph_bytes=1)):
            with self.subTest(limits=limits),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);(out/'graphs').mkdir();service,candidate=service_candidate()
                before=identity_state(service.identities);storage=GraphStorage(out,limits,30)
                with self.assertRaisesRegex(ValueError,'budget exceeded'):
                    storage.generate(service,candidate,out/'graphs/g0')
                self.assertEqual(identity_state(service.identities),before)
                self.assertFalse(service.consumed)
                self.assertTrue((out/'graph-replay/g0/recipe.json.gz').exists())


if __name__=='__main__':unittest.main()
