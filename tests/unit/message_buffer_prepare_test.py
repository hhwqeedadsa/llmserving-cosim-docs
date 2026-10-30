"""Public preparation supports distinct bounded sources without cloning."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]


class PreparationChecks(unittest.TestCase):
    def command(self, out, *extra):
        return subprocess.run([sys.executable,'-B',str(ROOT/'examples/scaleout_message_buffers/prepare.py'),
            '--output',str(out),*map(str,extra)],cwd=ROOT,capture_output=True,text=True,
            env=dict(os.environ,PYTHONPATH=str(ROOT/'python')),timeout=30)

    def manifest(self, folder, count=16):
        m=json.loads((ROOT/'examples/scaleout_route_replay/manifest.json').read_bytes())
        m['model'].update(name='deepseek-ai/DeepSeek-R1',hidden_dim=7168,num_layers=5)
        m['source']['collector_model_id']=m['model']['name']
        m['layers']=[dict(layer_id=i,kind='moe',num_experts=256,top_k=8) for i in range(5)]
        m['files']=[]
        for i in range(count):
            raw=[{str(layer):[[(i*17+entry*23+layer*7+token*13+j*29)%256 for j in range(8)]
                              for token in range(2 if entry==0 else 1)] for layer in range(5)} for entry in range(3)]
            p=folder/f'{i}.json';p.write_text(json.dumps(raw))
            m['files'].append(dict(file_id=f'functional-{i}',original_path=f'synthetic/{i}.json',
                file=dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()),format='token_layer_map_v1'))
        path=folder/'manifest.json';path.write_text(json.dumps(m));return path

    def test_default_synthetic_still_has_four_sources_and_original_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'out';r=self.command(out);self.assertEqual(r.returncode,0,r.stderr)
            c=json.loads((out/'service-buffers.json').read_bytes())
            self.assertEqual(len(c['route_replay']['requests']),4)
            self.assertEqual(list(map(len,c['parallel']['groups'])),[64,64])
            self.assertEqual(c['transport']['limits']['max_work'],512)

    def test_fifteen_and_sixteen_distinct_sources_cover_all_groups_and_connections(self):
        for count in (4,15,16):
            with self.subTest(count=count),tempfile.TemporaryDirectory() as directory:
                folder=Path(directory);manifest=self.manifest(folder);out=folder/'out'
                r=self.command(out,'--source-manifest',manifest,'--source-base',folder,'--route-files',count)
                self.assertEqual(r.returncode,0,r.stderr)
                c=json.loads((out/'service-buffers.json').read_bytes())
                q=c['route_replay']['requests'];self.assertEqual([x['rank_id'] for x in q],list(range(count)))
                self.assertEqual(len({x['file_id'] for x in q}),count)
                self.assertEqual([sum(x['rank_id']%4==i for x in q) for i in range(4)],
                                 [sum(j%4==i for j in range(count)) for i in range(4)])
                self.assertEqual(c['transport']['limits']['max_work'],512 if count==4 else 4096)
                self.assertEqual(c['source_ack_watchdog']['max_messages'],512 if count==4 else 4096)
                # The public preparer already calls full platform validation,
                # including every original/helper stream and contribution bound.
                self.assertEqual(c['route_replay']['budget']['max_files'],count)

    def test_invalid_count_or_missing_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for count in (3,17,15):
                r=self.command(folder/f'bad-{count}','--route-files',count)
                self.assertNotEqual(r.returncode,0)
                self.assertFalse((folder/f'bad-{count}').exists())
            manifest=self.manifest(folder,4)
            r=self.command(folder/'short','--source-manifest',manifest,'--source-base',folder,'--route-files',15)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('requested number',r.stderr)


if __name__=='__main__':
    unittest.main()
