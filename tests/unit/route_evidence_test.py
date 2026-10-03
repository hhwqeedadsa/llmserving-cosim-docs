from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.route_evidence import load_manifest,select_routes

ROOT=Path(__file__).resolve().parents[2]


def ref(path):return dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class RouteEvidenceTest(unittest.TestCase):
    def test_all_records_and_prefill_decode_index(self):
        config=json.loads((ROOT/'fixtures/m9_evidence/service.json').read_text())
        normalized,records,evidence=select_routes(config['route_manifest'],config['requests'],
            [g['model_spec'] for g in config['service']['groups']],[1,2,3])
        self.assertEqual(len(records),36)
        record=records['203',8,2]
        self.assertEqual(record['source_entry'],2);self.assertEqual(record['decode_index'],1)
        self.assertEqual(record['expert_ids'],[0,3])
        self.assertFalse(evidence['infer_eos_from_file_end']);self.assertTrue(evidence['route_data_is_synthetic'])
        self.assertEqual(normalized[2]['prompt_tokens'],7)
        self.assertEqual(normalized[2]['generated_tokens'],2)

    def test_bad_raw_layout_and_source_metadata(self):
        manifest=json.loads((ROOT/'fixtures/m9_evidence/manifest.json').read_text())
        for mutate in (
            lambda raw:raw[1].pop('2'),lambda raw:raw[1]['1'][0].__setitem__(0,999),
            lambda raw:raw[1]['1'][0].__setitem__(0,raw[1]['1'][0][1]),
            lambda raw:raw[1]['1'].append([0,1]),lambda raw:raw[0]['2'].pop(),
            lambda raw:raw[1].update({'0':[[0,1]]}),lambda raw:raw.__setitem__(1,None)):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);m=deepcopy(manifest)
                raw=json.loads((ROOT/m['files'][0]['file']['path']).read_text());mutate(raw)
                (p/'raw.json').write_text(json.dumps(raw));m['files'][0]['file']=ref(p/'raw.json')
                (p/'manifest.json').write_text(json.dumps(m))
                with self.subTest(mutate=mutate),self.assertRaises(ValueError):load_manifest(ref(p/'manifest.json'))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'manifest.json';m=deepcopy(manifest)
            m['source']['kind']='published_model_trace';m['source']['dataset_revision']='main'
            p.write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'fixed dataset revision'):load_manifest(ref(p))
            m['source']['kind']='captured_model_trace';m['source']['dataset_revision']='a'*40;p.write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'fixed weights/runtime'):load_manifest(ref(p))

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'manifest.json';p.write_text('{"schema_version":"a","schema_version":"b"}')
            with self.assertRaisesRegex(ValueError,'Duplicate JSON'):load_manifest(ref(p))


if __name__=='__main__':unittest.main()
