"""Archive and first-round boundaries, independent of the native backend."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from moe_ub_cosim.research_slice import _extract, _first_round


class SliceBoundaries(unittest.TestCase):
    def test_extract_hardlink_and_reject_corrupt_or_escaping_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); body = b'preserve the generated graph\n'
            meta = dict(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
            for label, filename, corrupt in [('valid', 'nested/a', False), ('escape', '../a', False), ('corrupt', 'nested/a', True)]:
                archive = root/(label+'.tar.gz')
                with tarfile.open(archive, 'w:gz') as stream:
                    item = tarfile.TarInfo(filename); item.size = len(body)
                    stream.addfile(item, io.BytesIO(body))
                    link = tarfile.TarInfo('nested/b'); link.type = tarfile.LNKTYPE; link.linkname = filename
                    stream.addfile(link)
                entries = {filename: dict(meta), 'nested/b': dict(meta)}
                if corrupt:
                    entries[filename]['sha256'] = '0'*64
                manifest = root/(label+'.json.gz')
                with gzip.open(manifest, 'wt') as stream:
                    json.dump(dict(files=entries), stream)
                a = dict(path=archive.name, inventory=manifest.name)
                if label == 'valid':
                    _extract(root, a, root/label)
                    first, second = root/label/filename, root/label/'nested/b'
                    self.assertEqual(first.read_bytes(), body)
                    self.assertEqual(first.stat().st_ino, second.stat().st_ino)
                else:
                    with self.assertRaises(ValueError):
                        _extract(root, a, root/label)

    def test_reject_later_round_or_arrival(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'protocol.jsonl'
            graph = dict(graph_id='g0', wave_id='w0')
            rows = [dict(type='COMMIT', now_ns=0, run_id='test', submissions=[graph], next_wakeup_ns=None, service_finished=False),
                    dict(type='DECIDE', completed=[dict(kind='WAVE_DONE', **graph)]),
                    dict(type='COMMIT', now_ns=10, submissions=[], next_wakeup_ns=None, service_finished=True)]
            def write():
                path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            write(); self.assertEqual(_first_round(path), ('test', [graph]))
            rows[-1]['submissions'] = [dict(graph_id='g1', wave_id='w1')]; write()
            with self.assertRaisesRegex(ValueError, 'complete first round'):
                _first_round(path)
            rows[-1]['submissions'] = []; rows[0]['next_wakeup_ns'] = 4; write()
            with self.assertRaisesRegex(ValueError, 'complete first round'):
                _first_round(path)


if __name__ == '__main__':
    unittest.main()
