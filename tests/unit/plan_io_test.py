"""Equivalent JSON/Chakra storage and actual aggregate disk-limit enforcement."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from full_model_storage_test import cases, build
from moe_ub_cosim.generate import export_plan
from moe_ub_cosim.host_probe import probe
from moe_ub_cosim.plan_io import compact_plan_chunks, file_sha256, write_plan_json


class PlanIOTest(unittest.TestCase):
    def test_legacy_bytes_compact_equivalence_and_manifest(self):
        for name, bundle in cases():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);plan=build(bundle)
                # Non-ASCII escaping and nested source evidence also survive.
                plan['input_provenance']={'label':'来源', 'rows':[{'value':None,'ok':True}]}
                before=deepcopy(plan)
                pretty=export_plan(plan,out/'pretty')
                compact=export_plan(plan,out/'compact',compact=True)
                self.assertEqual(plan,before)
                self.assertEqual((pretty/'plan.json').read_bytes(),(json.dumps(plan,indent=2)+'\n').encode())
                self.assertEqual((compact/'plan.json').read_bytes(),(json.dumps(plan,separators=(',',':'))+'\n').encode())
                self.assertEqual(json.loads((compact/'plan.json').read_text()),plan)
                self.assertLess((compact/'plan.json').stat().st_size,(pretty/'plan.json').stat().st_size)
                for p in pretty.glob('layer.*.et'):
                    self.assertEqual(p.read_bytes(),(compact/p.name).read_bytes())
                manifest=json.loads((compact/'generation.json').read_text())
                for p in [compact/'plan.json',*compact.glob('layer.*.et')]:
                    self.assertEqual(manifest['hashes'][p.name],hashlib.sha256(p.read_bytes()).hexdigest())

    def test_table_chunks_and_hashes_do_not_materialize_full_document(self):
        record={'node_id':1,'deps':list(range(128)),'name':'中文'}
        plan={'layers':[{'rank_graphs':[{'nodes':[record]*2000}]}]}
        pieces=compact_plan_chunks(plan)
        self.assertLess(max(map(len,pieces)),2048)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'plan.json'
            result=write_plan_json(plan,path,compact=True)
            with patch.object(Path,'read_bytes',side_effect=AssertionError('Whole-file read')):
                self.assertEqual(file_sha256(path),result['sha256'])
            self.assertEqual(result['bytes'],path.stat().st_size)
            for compact in (False,True):
                with self.assertRaisesRegex(ValueError,'max_plan_bytes'):
                    write_plan_json(plan,path,compact=compact,max_bytes=100)

    def test_aggregate_budget_rejects_existing_artifacts_before_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'existing').write_bytes(b'x'*8192)
            marker=root/'started'
            result=probe([sys.executable,'-c',f'open({str(marker)!r},"w").close()'],root/'probe',
                rss_bytes=256*2**20,address_bytes=512*2**20,artifact_bytes=4096,wall_seconds=10,
                artifact_roots=[root,root/'probe'])
            self.assertEqual(result['status'],'artifact_limit');self.assertFalse(marker.exists())
            self.assertEqual(result['artifact_roots'],[str(root)])
            self.assertEqual(result['remaining_live_group_pids'],[])

    def test_aggregate_budget_watches_sibling_output_during_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'sibling';other=root/'other'
            result=probe([sys.executable,'-c',f'import time;open({str(path)!r},"wb").write(b"x"*60000);open({str(other)!r},"wb").write(b"x"*60000);time.sleep(10)'],root/'probe',
                rss_bytes=256*2**20,address_bytes=512*2**20,artifact_bytes=100000,wall_seconds=5,artifact_roots=[root])
            self.assertEqual(result['status'],'artifact_limit')
            self.assertEqual(result['remaining_live_group_pids'],[])

    def test_free_space_reserve_rejects_before_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);marker=root/'started'
            result=probe([sys.executable,'-c',f'open({str(marker)!r},"w").close()'],root/'probe',
                rss_bytes=256*2**20,address_bytes=512*2**20,artifact_bytes=2**20,wall_seconds=10,
                min_free_bytes=2**63)
            self.assertEqual(result['status'],'disk_free_limit');self.assertFalse(marker.exists())


if __name__=='__main__':unittest.main()
