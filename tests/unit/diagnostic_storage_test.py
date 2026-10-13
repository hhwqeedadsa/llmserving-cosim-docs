import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.diagnostic_storage import compress_closed_logs
from moe_ub_cosim.user_io import read


class DiagnosticStorageTest(unittest.TestCase):
    def test_lossless_closed_rotations_and_empty_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); logs=root/'log';logs.mkdir()
            content={'log.log':b'\x00\xff\n'*100000,'log.1.log':b'prior rotation\n'*20000,'err.log':b''}
            (root/'result.json').write_bytes(b'structured records stay unchanged')
            for name,data in content.items():(logs/name).write_bytes(data)
            report=compress_closed_logs(root)
            self.assertTrue(report['completed']);self.assertEqual(report,read(root/'diagnostic-storage.json'))
            for name,data in content.items():
                item=report['files']['log/'+name]
                self.assertEqual(item['source'],dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
                self.assertEqual(gzip.decompress((root/item['compressed']['path']).read_bytes()),data)
                self.assertFalse((logs/name).exists())
            self.assertEqual((root/'result.json').read_bytes(),b'structured records stay unchanged')
            with self.assertRaises(ValueError):compress_closed_logs(root)

    def test_invalid_members_rejected_before_deletion(self):
        for kind in ('symlink','directory','existing-gzip'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);logs=root/'log';logs.mkdir();source=logs/'log.log';source.write_bytes(b'keep')
                bad=logs/'unexpected.log'
                if kind=='symlink':bad.symlink_to(source)
                elif kind=='directory':bad.mkdir()
                else:(logs/'log.log.gz').write_bytes(b'existing')
                with self.assertRaises(ValueError):compress_closed_logs(root)
                self.assertEqual(source.read_bytes(),b'keep')
                self.assertFalse((root/'diagnostic-storage.json').exists())

    def test_failed_verification_retains_original_and_incomplete_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'log').mkdir();source=root/'log/log.log';source.write_bytes(b'keep')
            with patch('moe_ub_cosim.diagnostic_storage.gzip.open',side_effect=OSError('read failure')):
                with self.assertRaises(OSError):compress_closed_logs(root)
            self.assertEqual(source.read_bytes(),b'keep')
            self.assertFalse(read(root/'diagnostic-storage.json')['completed'])

    def test_symlink_directory_rejected_and_absent_directory_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'elsewhere').mkdir();(root/'log').symlink_to(root/'elsewhere',target_is_directory=True)
            with self.assertRaises(ValueError):compress_closed_logs(root)
            (root/'log').unlink()
            self.assertEqual(compress_closed_logs(root)['files'],{})


if __name__=='__main__':unittest.main()
