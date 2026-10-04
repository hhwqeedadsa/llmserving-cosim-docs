"""Closed full-model export keeps validation and mutable-plan rejection intact."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

from full_model_storage_test import cases
from moe_ub_cosim import full_model, generate
from moe_ub_cosim.full_round_probe import build_export, instrument
from moe_ub_cosim.plan import RunIdentities

ROOT=Path(__file__).resolve().parents[2]


def legacy_exporter():
    baseline=json.loads((ROOT/'fixtures/m10_full_model_export/baseline.json').read_text())
    source=subprocess.check_output(['git','show',baseline['baseline_commit']+':python/moe_ub_cosim/generate.py'],cwd=ROOT)
    assert hashlib.sha256(source).hexdigest()==baseline['source_sha256']['python/moe_ub_cosim/generate.py']
    module=types.ModuleType('moe_ub_cosim._legacy_generate');module.__package__='moe_ub_cosim'
    module.__file__=str(ROOT/'python/moe_ub_cosim/generate.py')
    exec(compile(source,'legacy-generate.py','exec'),module.__dict__)
    return module.export_plan


def ids():return RunIdentities(transfer=2**40,task=2000,tag=4000)


def annotate(plan,evidence):
    plan['input_provenance']=deepcopy(evidence)
    for item in [plan,*plan['layers']]:
        item['route_source']='published_model_trace'
        item['compute_source']='full_model_stage_reference_with_context_cost'


class FullModelExportTest(unittest.TestCase):
    def test_old_and_closed_artifacts_and_input_isolation(self):
        legacy=legacy_exporter()
        for name,b in cases():
            # Explicit synthetic evidence tests copying; it is not a real trace.
            evidence=dict(test_only='synthetic',records=[dict(index=1)]) if name=='dense-between' else None
            original=deepcopy(b)
            with self.subTest(case=name),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)
                plan=full_model.build_full_model(b['configs'],ids(),b['provenance'],b['model'],b['profile'])
                if evidence is not None:annotate(plan,evidence)
                legacy(plan,out/'old')
                with patch.object(full_model,'validate_full_model',wraps=full_model.validate_full_model) as validation, \
                     patch.object(full_model,'compose_full_model',wraps=full_model.compose_full_model) as composition:
                    actual,directory=generate.build_export_full_model(b['configs'],ids(),b['provenance'],b['model'],b['profile'],out/'new',source_evidence=evidence)
                self.assertEqual(validation.call_count,1)
                self.assertEqual(composition.call_count,2)  # Build and full reconstruction; no third pass.
                self.assertEqual(plan,actual);self.assertEqual(b,original)
                for path in [out/'old/plan.json',out/'old/generation.json',*sorted((out/'old').glob('layer.*.et'))]:
                    self.assertEqual(path.read_bytes(),(directory/path.name).read_bytes(),path.name)
                if evidence is not None:
                    evidence['records'].clear();self.assertEqual(plan,actual)

    def test_modified_returned_or_plain_plan_still_rejected_before_writing(self):
        _,b=next(cases())
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            plan,_=generate.build_export_full_model(b['configs'],ids(),b['provenance'],b['model'],b['profile'],out/'valid')
            changes=[lambda p:p['rank_graphs'][0]['nodes'][0].update(duration_ns=999),
                lambda p:next(n for n in p['rank_graphs'][0]['nodes'] if n['deps'])['deps'].clear(),
                lambda p:p['transfers'][0]['sources'].clear(),
                lambda p:p['layers'][0]['transfers'][0].update(payload_bytes=1),
                lambda p:p['full_model_profile']['layers'][0]['per_token_ns'].update(dense_ffn=0)]
            for i,change in enumerate(changes):
                for plain in (False,True):
                    bad=json.loads(json.dumps(plan)) if plain else deepcopy(plan);change(bad)
                    directory=out/f'bad-{i}-{plain}'
                    with self.subTest(change=i,plain=plain),self.assertRaises(ValueError):generate.export_plan(bad,directory)
                    self.assertFalse(directory.exists())

    def test_failed_build_and_write_do_not_create_success_manifest(self):
        _,b=next(cases())
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);bad=deepcopy(b);bad['configs'][0]['tokens'][0]['expert_ids']=[999]
            with self.assertRaises(ValueError):generate.build_export_full_model(bad['configs'],ids(),bad['provenance'],bad['model'],bad['profile'],out/'invalid')
            self.assertFalse((out/'invalid').exists())
            # Force a real writer error after validation. No cached approval is
            # retained and the completed generation manifest must be absent.
            with patch.object(generate,'write_message',side_effect=OSError('test disk failure')),self.assertRaises(OSError):
                generate.build_export_full_model(b['configs'],ids(),b['provenance'],b['model'],b['profile'],out/'failed')
            self.assertFalse((out/'failed/generation.json').exists())
            generate.build_export_full_model(b['configs'],ids(),b['provenance'],b['model'],b['profile'],out/'retry')
            self.assertTrue((out/'retry/generation.json').exists())

    def test_probe_hooks_preserve_closed_export_and_restore_writer(self):
        _,b=next(cases());original=generate._write_plan
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);events=[]
            build_export(b,out,lambda phase,**row:events.append(dict(phase=phase,**row)))
            self.assertIs(generate._write_plan,original)
            self.assertEqual([r['call'] for r in events if r['phase']=='composition_end'],[1,2])
            self.assertEqual([r['phase'] for r in events[-4:]],['export_begin','write_begin','write_end','export_end'])
            with self.assertRaises(ValueError):build_export(b,out,lambda *a,**kw:None,expected={})
            self.assertIs(generate._write_plan,original)
            with patch.object(generate,'_write_plan',side_effect=OSError('test write failure')) as writer:
                with self.assertRaises(OSError),instrument(lambda *a,**kw:None):generate.export_plan(json.loads((out/'graph/plan.json').read_text()),out/'error')
                self.assertIs(generate._write_plan,writer)
            self.assertIs(generate._write_plan,original)


if __name__=='__main__':unittest.main()
