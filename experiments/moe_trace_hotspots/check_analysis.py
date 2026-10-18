"""Small hand-counted checks using synthetic routes, never published statistics."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hotspot_analysis', Path(__file__).with_name('analyze.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MANIFEST = Path('fixtures/m9_evidence/manifest.json')


class AnalysisChecks(unittest.TestCase):
    def test_hand_counted_phases_and_layer_local_identity(self):
        r = module.analyze(MANIFEST, ['201'], window=1, top_m=1)
        self.assertFalse(r['real_trace'])
        self.assertFalse(r['network_executed'])
        self.assertFalse(r['windows_are_physical_time'])
        layers = {v['layer_id']: v for v in r['aggregate']}
        p, d = (layers[1]['phases'][k] for k in ('prefill', 'decode'))
        self.assertEqual(p['counts'], [1, 2, 1, 0])
        self.assertEqual((p['tokens'], p['selections'], p['inactive_experts']), (2, 4, 1))
        self.assertEqual(d['counts'], [1, 1, 2, 2])
        self.assertEqual(d['tokens'], 3)
        self.assertAlmostEqual(d['max_to_mean'], 4/3)
        self.assertAlmostEqual(d['activation_probability'][2], 2/3)
        self.assertAlmostEqual(d['selection_share'][2], 1/3)
        self.assertEqual(layers[2]['phases']['decode']['counts'], [2, 2, 2, 0])
        row = r['requests'][0]['layers'][0]
        self.assertEqual([w['top_experts'] for w in row['decode_token_windows']], [[0], [1], [2]])
        self.assertEqual(row['decode_token_windows'][1]['previous_top_jaccard'], 0)
        self.assertEqual(row['prefill_decode_top_jaccard'], 0)
        self.assertEqual(row['top_coactivated_pairs']['decode'][0],
                         dict(expert_ids=[0, 2], token_count=1))

    def test_conservation_includes_idle_experts(self):
        r = module.analyze(MANIFEST)
        for row in r['aggregate']:
            for phase in row['phases'].values():
                self.assertEqual(len(phase['counts']), row['num_experts'])
                self.assertEqual(sum(phase['counts']), phase['tokens']*row['top_k'])
                self.assertAlmostEqual(sum(phase['selection_share']), 1)

    def test_no_implicit_sampling_or_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, 'Too many files'):
            module.analyze(MANIFEST, max_files=1)
        with self.assertRaisesRegex(ValueError, 'Unknown file ID'):
            module.analyze(MANIFEST, ['not-present'])

    def test_source_hash_and_full_file_shape(self):
        source = json.loads(MANIFEST.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = deepcopy(source); manifest['files'] = [manifest['files'][0]]
            original = Path(manifest['files'][0]['file']['path']).read_bytes()
            raw = root/'raw.json'; raw.write_bytes(original+b' ')
            manifest['files'][0]['file']['path'] = str(raw)
            path = root/'manifest.json'; path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                module.analyze(path)
            broken = json.loads(original); del broken[-1]['2']
            raw.write_text(json.dumps(broken))
            manifest['files'][0]['file']['sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest()
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'Missing/extra source layer'):
                module.analyze(path)


if __name__ == '__main__':
    unittest.main()
