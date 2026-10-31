import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zlib

from moe_ub_cosim.modern_deepep_plan import execute,check,expand_saved_plan,MAX_EXPANDED_PLAN

ROOT=Path(__file__).resolve().parents[2]


class ModernPlanStorage(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.config=ROOT/'examples/modern_deepep_partial_order/input.json'
        execute(self.config,self.root/'flat');execute(self.config,self.root/'compressed',format='compressed-json')

    def tearDown(self):self.temp.cleanup()

    def test_lossless_default_and_identical_closure_with_withheld_event(self):
        flat=(self.root/'flat/plan.json').read_bytes();stored=(self.root/'compressed/plan.json.zlib').read_bytes()
        self.assertEqual(zlib.decompress(stored),flat);self.assertLess(len(stored),len(flat))
        self.assertFalse((self.root/'compressed/plan.json').exists())
        old=json.loads((self.root/'flat/manifest.json').read_bytes());new=json.loads((self.root/'compressed/manifest.json').read_bytes())
        self.assertEqual(old['schema_version'],'moe-modern-deepep-manifest/2')
        self.assertEqual(old['plan_sha256'],new['plan_sha256'])
        for suffix,withheld in [('all',[]),('zero',['m0.dispatch.expert_count.s2.d1.visible'])]:
            a=check(self.root/'flat',self.root/(suffix+'-flat.json'),withheld)
            b=check(self.root/'compressed',self.root/(suffix+'-compressed.json'),withheld)
            self.assertEqual(a,b)
        self.assertFalse(b['all_completions_reachable'])

    def corrupt(self,name,mutate_bytes=None,mutate_manifest=None):
        directory=self.root/name;shutil.copytree(self.root/'compressed',directory)
        manifest=json.loads((directory/'manifest.json').read_bytes())
        if mutate_bytes:
            raw=mutate_bytes((directory/'plan.json.zlib').read_bytes());(directory/'plan.json.zlib').write_bytes(raw)
            manifest.update(stored_plan_bytes=len(raw),stored_plan_sha256=hashlib.sha256(raw).hexdigest())
        if mutate_manifest:mutate_manifest(manifest)
        (directory/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises((ValueError,json.JSONDecodeError)):check(directory,self.root/(name+'-check.json'))
        self.assertFalse((self.root/(name+'-check.json')).exists())

    def test_structural_corruption_rejected_even_with_updated_stored_hash(self):
        for name,mutate in [('truncated',lambda b:b[:-2]),('trailing',lambda b:b+b'x'),
                            ('concatenated',lambda b:b+zlib.compress(b'{}')),('invalid',lambda b:b'not-zlib')]:
            with self.subTest(name=name):self.corrupt(name,mutate_bytes=mutate)

    def test_bad_manifest_dimensions_and_filename_rejected(self):
        for index,(key,value) in enumerate([('encoding','other'),('plan_file','../flat/plan.json'),
                          ('plan_bytes',MAX_EXPANDED_PLAN+1),('plan_bytes',0),('plan_bytes',True),
                          ('plan_bytes',1),('stored_plan_bytes',1),('schema_version','unknown')]):
            with self.subTest(key=key,value=value):
                self.corrupt('bad-'+str(index),mutate_manifest=lambda row,k=key,v=value:row.update({k:v}))

    def test_expanded_size_bound_applies_before_json_or_compiler(self):
        with self.assertRaises(ValueError):expand_saved_plan(zlib.compress(b'a'*1000000),16)
        with self.assertRaises(ValueError):expand_saved_plan(zlib.compress(b'{}'),MAX_EXPANDED_PLAN+1)

    def test_semantic_tampering_with_all_hashes_recomputed_is_rejected(self):
        raw=json.loads((self.root/'flat/plan.json').read_bytes());raw['operations'].pop()
        changed=(json.dumps(raw,separators=(',',':'))+'\n').encode();stored=zlib.compress(changed)
        self.corrupt('semantic',mutate_bytes=lambda _:stored,mutate_manifest=lambda m:m.update(
            plan_bytes=len(changed),plan_sha256=hashlib.sha256(changed).hexdigest()))

    def test_saved_output_budget_not_bypassed_and_unknown_format_rejected(self):
        config=json.loads(self.config.read_bytes());config['limits']['max_output_bytes']=1024
        path=self.root/'small.json';path.write_text(json.dumps(config))
        with self.assertRaises(ValueError):execute(path,self.root/'small',format='compressed-json')
        self.assertFalse((self.root/'small').exists())
        with self.assertRaises(ValueError):execute(self.config,self.root/'unknown',format='other')


if __name__=='__main__':unittest.main()
