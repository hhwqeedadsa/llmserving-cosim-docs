"""Packed routes retain independent source evidence and exact consumption checks."""
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim import route_evidence as routes
from moe_ub_cosim.route_records import RouteDomain, RouteUsage

ROOT = Path(__file__).resolve().parents[2]


def ref(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class RouteRecordsTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'fixtures/m9_evidence/service.json').read_text())
        self.models = [g['model_spec'] for g in self.config['service']['groups']]

    def select(self, **kwargs):
        return routes.select_routes(self.config['route_manifest'], self.config['requests'],
                                    self.models, [1, 2, 3], **kwargs)

    def test_every_record_matches_raw_and_request_order(self):
        self.config['requests'].reverse()  # Manifest order must not reorder requests/records.
        normal, records, evidence = self.select(compact=True)
        manifest, sources = routes.load_manifest(self.config['route_manifest'])
        expected = {}
        for request, selected in zip(self.config['requests'], normal):
            source = sources[request['file_id']]
            self.assertEqual(request['request_id'], selected['request_id'])
            for step in range(request['decode_steps']):
                entry = request['first_decode_entry'] + step
                index = source['prompt_tokens'] + entry - 1
                for layer in (1, 2, 3):
                    key = str(request['request_id']), index, layer
                    expected[key] = dict(request_id=key[0], phase='decode', token_index=index,
                        layer_id=layer, expert_ids=source['raw'][entry][str(layer)][0],
                        source_file=source['file'], file_id=request['file_id'], source_entry=entry,
                        source_layer_id=layer, source_token_row=0, decode_index=entry-1,
                        source_index_convention='entry_0_prefill_entry_1_first_decode')
        self.assertEqual(list(records), list(expected))
        self.assertEqual(dict(records), expected)
        regular = self.select()
        self.assertIsInstance(regular[1], dict)
        self.assertEqual(regular, (normal, expected, evidence))
        self.assertEqual(records.storage_counts(), dict(source_records=len(self.config['requests']),
            logical_records=36, expert_storage_bytes=288))
        key = next(iter(records))
        snapshot = deepcopy(records[key])
        normal[0]['decode_routes'][0]['1'].clear()
        read = records[key]
        read['expert_ids'].clear(); read['source_file'].clear()
        self.assertEqual(records[key], snapshot)
        with self.assertRaises(KeyError):
            records['missing', 0, 1]

    def test_all_sources_checked_but_at_most_two_raw_traces_live(self):
        original = routes.read_json
        live = set(); peaks = []; read_files = []
        class Tracked(list):
            def __init__(self, raw):
                super().__init__(raw); live.add(id(self)); peaks.append(len(live))
            def __del__(self):
                live.discard(id(self))
        def read_checked(reference):
            path, raw = original(reference)
            if isinstance(raw, list):
                read_files.append(path); raw = Tracked(raw)
            return path, raw
        with patch.object(routes, 'read_json', side_effect=read_checked):
            self.select(compact=True)
        gc.collect()
        manifest = json.loads((ROOT / 'fixtures/m9_evidence/manifest.json').read_text())
        self.assertEqual(len(read_files), len(manifest['files']))
        self.assertLessEqual(max(peaks), 2)
        self.assertFalse(live)
        self.config['requests'] = self.config['requests'][:1]
        selected = self.config['requests'][0]['file_id']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unused = next(f for f in manifest['files'] if f['file_id'] != selected)
            raw = json.loads((ROOT / unused['file']['path']).read_text())
            raw[1]['1'][0][0] = 999
            (root / 'bad.json').write_text(json.dumps(raw))
            unused['file'] = ref(root / 'bad.json')
            (root / 'manifest.json').write_text(json.dumps(manifest))
            self.config['route_manifest'] = ref(root / 'manifest.json')
            with self.assertRaisesRegex(ValueError, 'Invalid source expert choices'):
                self.select(compact=True)

    def test_duplicate_and_unknown_selection_rejected(self):
        self.config['requests'].append(deepcopy(self.config['requests'][0]))
        with self.assertRaisesRegex(ValueError, 'reused source'):
            self.select(compact=True)
        self.config['requests'][-1]['file_id'] = 'unknown-file'
        with self.assertRaisesRegex(ValueError, 'Unknown or reused'):
            self.select(compact=True)

    def test_consumption_exact_domain_and_independent_bits(self):
        normal, records, _ = self.select(compact=True)
        domain = RouteDomain(normal, [1, 2, 3])
        used = RouteUsage(domain)
        expected = set(records)
        self.assertEqual(set(domain), expected)
        self.assertEqual(len(used.bits), (len(expected) + 7) // 8)
        self.assertNotEqual(used, domain)
        keys = list(records)
        used.update(keys[:-1])
        self.assertNotIn(keys[-1], used)
        self.assertNotEqual(used, domain)
        with self.assertRaisesRegex(ValueError, 'consumed twice'):
            used.update([keys[0]])
        with self.assertRaises(KeyError):
            used.update([('unknown', 0, 1)])
        used.update(keys[-1:])
        self.assertEqual(used, domain)
        self.assertEqual(set(used), expected)
        self.assertEqual(len(used), len(expected))
        another = RouteUsage(domain)
        self.assertNotEqual(another, used)

    def test_cli_streams_same_normalized_records_and_output_hash(self):
        manifest, sources = routes.load_manifest(self.config['route_manifest'])
        expected = []
        for file_id, source in sources.items():
            for entry in range(1, len(source['raw'])):
                for layer in manifest['layers']:
                    if layer['kind'] == 'dense':
                        continue
                    expected.append(dict(file_id=file_id, phase='decode', decode_index=entry-1,
                        token_index=source['prompt_tokens']+entry-1, layer_id=layer['layer_id'],
                        expert_ids=source['raw'][entry][str(layer['layer_id'])][0],
                        source_file=source['file'], source_entry=entry, source_token_row=0))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'import'
            original = Path.read_bytes
            def no_output_materialization(path):
                if path == output / 'normalized_decode.jsonl':
                    raise AssertionError('Whole normalized output read')
                return original(path)
            with patch('sys.argv', ['route_evidence', '--manifest', self.config['route_manifest']['path'],
                                     '--output-dir', str(output)]), patch.object(Path, 'read_bytes', no_output_materialization):
                routes.main()
            content = (output / 'normalized_decode.jsonl').read_bytes()
            self.assertEqual([json.loads(line) for line in content.splitlines()], expected)
            report = json.loads((output / 'report.json').read_text())
            self.assertEqual(report['normalized_sha256'], hashlib.sha256(content).hexdigest())
            self.assertEqual(report['records'], len(expected))


if __name__ == '__main__':
    unittest.main()
