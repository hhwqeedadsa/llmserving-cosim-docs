"""Selection order, eligibility and source integrity without any network access."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from moe_ub_cosim.route_evidence import select_routes
from moe_ub_cosim.route_selection import download_plan,select

ROOT=Path(__file__).resolve().parents[2]
def save(p,x):p.write_text(json.dumps(x));return p
def ref(p):return dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())


class RouteSelectionTest(unittest.TestCase):
    def setup_case(self,p):
        source=json.loads((ROOT/'fixtures/m9_evidence/manifest.json').read_text())
        prefix=source['source']['collector_model_id']+'/mmlu/';files=[]
        for i in range(6):
            raw=[{'0':None,**{str(l):[[i%4,(i+1)%4] for _ in range(i+1 if step==0 else 1)] for l in (1,2,3)}} for step in range(5)]
            path=p/'data'/prefix/(str(i)+'.json');path.parent.mkdir(parents=True,exist_ok=True);save(path,raw)
            files.append(self.row(path,p/'data'))
        catalog=dict(schema_version='moe-ub-route-catalog/1',dataset_id=source['source']['dataset_id'],
            dataset_revision=source['source']['dataset_revision'],prefix=prefix,scope='complete_fixed_revision_subtree',
            complete=True,files=files,pages=[],checked_utc='synthetic')
        cp=save(p/'catalog.json',catalog);sp=save(p/'source.json',source)
        config=dict(schema_version='moe-ub-route-selection/1',catalog=ref(cp),source_manifest=ref(sp),data_root=str(p/'data'),
            selection=dict(algorithm='sha256_seed_newline_path_v1',seed=0,subset='mmlu',required_requests=3,
                min_decode_records=2,decode_steps=2,rank_ids=[2,7],arrival_ns=0),
            budget=dict(max_scan_files=6,max_file_bytes=10000,max_total_read_bytes=60000))
        return config,catalog

    def row(self,path,root):
        raw=path.read_bytes()
        return dict(path=path.relative_to(root).as_posix(),size=len(raw),
                    git_blob_id=hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest())

    def test_order_independent_of_catalog_listing_and_existing_source_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,cat=self.setup_case(p)
            report=select(c,p/'selected');manifest=json.loads((p/'selected/manifest.json').read_text())
            self.assertEqual([Path(x['original_path']).stem for x in manifest['files']],['4','5','0'])
            requests=json.loads((p/'selected/requests.json').read_text())
            self.assertEqual([r['rank_id'] for r in requests],[2,7,2])
            self.assertTrue(all(r['target_total_tokens'] is None and r['first_decode_entry']==1 for r in requests))
            self.assertEqual(report['selected_decode_layer_records'],18)
            self.assertEqual([(r['first_input_token_index'],r['last_input_token_index'],r['reserved_kv_tokens'],r['observed_tokens_reached'])
                for r in report['checked']],[(5,6,7,8),(6,7,8,9),(1,2,3,4)])
            model=dict(name=manifest['model']['name'],hidden_size=256,layers=manifest['layers'])
            normalized,records,evidence=select_routes(ref(p/'selected/manifest.json'),requests,[model],[1,2,3])
            self.assertEqual(len(records),18);self.assertEqual(records['1',5,1]['expert_ids'],[0,1])
            self.assertFalse(evidence['infer_eos_from_file_end'])
            cat['files'].reverse();save(p/'catalog.json',cat);c['catalog']=ref(p/'catalog.json')
            select(c,p/'reordered')
            self.assertEqual((p/'selected/manifest.json').read_bytes(),(p/'reordered/manifest.json').read_bytes())
            c['selection']['seed']=9;select(c,p/'seed9')
            self.assertEqual([Path(x['original_path']).stem for x in json.loads((p/'seed9/manifest.json').read_text())['files']],['0','4','1'])

    def test_reject_whole_bad_file_and_prefill_only_then_next_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,cat=self.setup_case(p)
            for i in (4,5):
                file=p/'data'/cat['files'][i]['path'];raw=json.loads(file.read_text())
                if i==4:raw[-1].pop('2')  # Even an unused late step must be valid.
                else:raw=raw[:1]
                save(file,raw);cat['files'][i]=self.row(file,p/'data')
            save(p/'catalog.json',cat);c['catalog']=ref(p/'catalog.json');r=select(c,p/'out')
            self.assertEqual([x['reason'] for x in r['checked'][:2]],['invalid_source_file','too_few_decode_records'])
            self.assertIn('source layer',r['checked'][0]['error'])
            self.assertEqual([Path(x['original_path']).stem for x in json.loads((p/'out/manifest.json').read_text())['files']],['0','2','1'])
            self.assertEqual(r['selected_files'],3)

    def test_missing_earlier_file_integrity_duplicates_and_budgets_are_fatal(self):
        for kind,diagnostic in [('missing','Missing earlier'),('hash','Git blob'),('duplicate','Repeated raw'),
                                ('insufficient','Insufficient'),('scan','Scan budget'),('bytes','byte budget')]:
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);c,cat=self.setup_case(p);file=p/'data'/cat['files'][4]['path']
                if kind=='missing':file.unlink()
                elif kind=='hash':file.write_bytes(file.read_bytes().replace(b'0',b'3',1))
                elif kind=='duplicate':
                    duplicate=p/'data'/cat['files'][5]['path'];duplicate.write_bytes(file.read_bytes());cat['files'][5]=self.row(duplicate,p/'data')
                elif kind=='insufficient':c['selection']['required_requests']=7;c['budget']['max_scan_files']=7
                elif kind=='scan':c['budget']['max_scan_files']=3;c['selection']['min_decode_records']=5
                else:c['budget']['max_total_read_bytes']=1
                save(p/'catalog.json',cat);c['catalog']=ref(p/'catalog.json')
                with self.assertRaisesRegex(ValueError,diagnostic):select(c,p/'out')
                self.assertFalse(json.loads((p/'out/report.json').read_text())['data_ready'])
                self.assertFalse((p/'out/manifest.json').exists());self.assertFalse((p/'out/requests.json').exists())

    def test_catalog_completeness_revision_path_and_bounded_download_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,cat=self.setup_case(p);plan=download_plan(c)
            self.assertEqual(len(plan['bounded_candidates']),6)
            self.assertEqual(plan['max_selected_candidate_bytes'],sum(r['size'] for r in cat['files']))
            self.assertFalse(plan['eligibility_verified'])
            for mutate in (lambda x:x.update(complete=False),lambda x:x.update(dataset_revision='different'),
                           lambda x:x['files'][0].update(path='../escape.json'),lambda x:x['files'].append(x['files'][0])):
                bad=deepcopy(cat);mutate(bad);save(p/'catalog.json',bad);c['catalog']=ref(p/'catalog.json')
                with self.assertRaises(ValueError):download_plan(c)
            save(p/'catalog.json',cat);c['catalog']=ref(p/'catalog.json');c['catalog']['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'SHA256'):download_plan(c)

    def test_acquisition_stops_at_required_valid_files_and_resumes_without_network(self):
        spec=importlib.util.spec_from_file_location('acquire',ROOT/'scripts/acquire_route_selection.py')
        acquire=importlib.util.module_from_spec(spec);spec.loader.exec_module(acquire)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);c,cat=self.setup_case(p)
            source=json.loads((p/'source.json').read_text());source['source'].update(kind='published_model_trace',dataset_revision='a'*40)
            save(p/'source.json',source);c['source_manifest']=ref(p/'source.json');cat['dataset_revision']='a'*40
            # First candidate is malformed. It must be retained exactly, but skipped whole.
            file=p/'data'/cat['files'][4]['path'];raw=json.loads(file.read_text());raw[-1].pop('2');save(file,raw)
            cat['files'][4]=self.row(file,p/'data');save(p/'catalog.json',cat);c['catalog']=ref(p/'catalog.json')
            originals={row['path']:(p/'data'/row['path']).read_bytes() for row in cat['files']}
            for path in originals:(p/'data'/path).unlink()
            save(p/'config.json',c);fetched=[]
            class Opener:
                def open(self,request,timeout):
                    path=unquote(request.full_url.split('/resolve/'+'a'*40+'/',1)[1]);fetched.append(path)
                    return io.BytesIO(originals[path])
            argv=['acquire','--input',str(p/'config.json'),'--output-dir',str(p/'acquired'),
                  '--max-download-bytes','60000','--workers','4']
            with patch.dict('os.environ',{'HF_TOKEN':'test-only-placeholder'}),patch('sys.argv',argv),\
                 patch.object(acquire.urllib.request,'build_opener',return_value=Opener()),patch('builtins.print'):
                acquire.main()
            report=json.loads((p/'acquired/report.json').read_text())
            self.assertEqual([Path(r['path']).stem for r in report['checked']],['4','5','0','2'])
            self.assertEqual((len(fetched),report['eligible_files']),(4,3))
            self.assertEqual(report['downloaded_bytes'],sum(len(originals[r]) for r in fetched))
            self.assertFalse(report['checked'][0]['raw_schema_valid'])
            self.assertEqual(file.read_bytes(),originals[cat['files'][4]['path']])
            self.assertFalse((p/'data'/cat['files'][1]['path']).exists())
            argv[4]=str(p/'resumed')
            with patch.dict('os.environ',{'HF_TOKEN':'test-only-placeholder'}),patch('sys.argv',argv),\
                 patch.object(acquire.urllib.request,'build_opener',side_effect=AssertionError('Unexpected network')),patch('builtins.print'):
                acquire.main()
            resumed=json.loads((p/'resumed/report.json').read_text())
            self.assertEqual(resumed['downloaded_bytes'],0);self.assertEqual(resumed['eligible_files'],3)
            argv[4]=str(p/'over-budget');argv[6]='1'
            with patch('sys.argv',argv),patch.object(acquire.urllib.request,'build_opener',side_effect=AssertionError('Unexpected network')):
                with self.assertRaisesRegex(ValueError,'authorized byte limit'):acquire.main()
            self.assertFalse((p/'over-budget').exists())


if __name__=='__main__':unittest.main()
