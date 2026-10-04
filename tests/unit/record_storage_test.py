import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from moe_ub_cosim.record_storage import iter_file, iter_result, iter_port_records, validate_policy


POLICY = dict(schema_version='moe-ub-record-storage/1', max_compressed_bytes=2**20,
              max_buffer_bytes=2**20, max_record_bytes=4096)


class RecordStorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root/'records').mkdir()

    def write(self, name, rows):
        chunks = [(json.dumps(row)+'\n').encode() for row in rows]
        data = b''.join(chunks)
        encoded = b''.join(gzip.compress(chunk) for chunk in chunks) if chunks else gzip.compress(b'')
        (self.root/'records'/name).write_bytes(encoded)
        return dict(name=name, records=len(rows), raw_bytes=len(data), compressed_bytes=len(encoded), crc32=zlib.crc32(data))

    def test_concatenated_members_and_order(self):
        refs = [self.write('part-0.gz', [{'x': 2}, {'x': 3}]), self.write('part-1.gz', []), self.write('part-2.gz', [{'x': 1}])]
        report = dict(schema_version='moe-ub-service-result/2', record_storage=dict(
            schema_version='moe-ub-result-records/1', directory='records', policy=POLICY,
            fields={'test': dict(records=3, files=refs)}))
        self.assertEqual(list(iter_result(self.root, 'test', report)), [{'x': 2}, {'x': 3}, {'x': 1}])
        report['record_storage']['fields']['test']['files'].append(refs[0])
        with self.assertRaises(ValueError):list(iter_result(self.root, 'test', report))

    def test_corruption_and_limits(self):
        ref = self.write('test.gz', [{'a': 'x'*100}]); path = self.root/'records'/ref['name']; original = path.read_bytes()
        for key in ('records', 'raw_bytes', 'crc32', 'compressed_bytes'):
            bad = dict(ref); bad[key] += 1
            with self.assertRaises(ValueError):list(iter_file(self.root, bad))
        with self.assertRaises(ValueError):list(iter_file(self.root, ref, 16))
        with self.assertRaises(ValueError):list(iter_file(self.root, dict(ref, name='../test.gz')))
        for changed in (original[:-4], original[:15]+bytes([original[15]^128])+original[16:]):
            path.write_bytes(changed)
            with self.assertRaises((ValueError, OSError, EOFError, zlib.error)):
                list(iter_file(self.root, dict(ref, compressed_bytes=len(changed))))
        path.write_bytes(original+b'garbage')
        with self.assertRaises((ValueError, OSError, EOFError)):
            list(iter_file(self.root, dict(ref, compressed_bytes=path.stat().st_size)))

    def test_sparse_zero_windows_and_tampering(self):
        port = self.root/'port'; (port/'observed').mkdir(parents=True)
        config = dict(switch_id=2, port_id=3, observation_start_local_ns=10,
                      observation_end_local_ns=61, window_period_ns=10)
        (port/'observed/public_config.json').write_text(json.dumps(config))
        ref = self.write('windows.gz', [dict(index=1, counted_packets=2, counted_packet_bytes=120),
                                       dict(index=5, counted_packets=1, counted_packet_bytes=60)])
        manifest = dict(schema_version='moe-ub-port-records/1', path_base='execution_directory', records_directory='records',
            fields={'port-windows.jsonl': ref}, window_encoding='sparse_nonzero_with_declared_zero_windows',
            logical_windows=6, padding_field=False)
        (port/'record-storage.json').write_text(json.dumps(manifest))
        rows = list(iter_port_records(self.root, port, 'observed/port_counters.jsonl'))
        self.assertEqual([r['counted_packets'] for r in rows], [0, 2, 0, 0, 0, 1])
        self.assertEqual(rows[-1]['local_window_end_ns'], 61)
        for indices in ([1, 1], [4, 2], [6], [-1]):
            manifest['fields']['port-windows.jsonl'] = self.write('windows.gz', [dict(index=i, counted_packets=1, counted_packet_bytes=60) for i in indices])
            (port/'record-storage.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):list(iter_port_records(self.root, port, 'observed/port_counters.jsonl'))

    def test_policy_and_legacy(self):
        self.assertEqual(validate_policy(POLICY), POLICY)
        for changed in (dict(max_buffer_bytes=1), dict(max_record_bytes=True), dict(max_compressed_bytes=-1), dict(extra=1)):
            with self.assertRaises(ValueError):validate_policy(dict(POLICY, **changed))
        self.assertEqual(list(iter_result(self.root, 'transfers', dict(schema_version='moe-ub-service-result/1', transfers=[{'a': 1}]))), [{'a': 1}])


if __name__ == '__main__':unittest.main()
