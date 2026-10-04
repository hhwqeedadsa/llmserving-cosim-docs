"""Shared inode optimization preserves checks for independent port files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.research_export import require_same_records


class SharedRegistryTest(unittest.TestCase):
    def test_hardlinks_copies_and_replaced_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / 'reference.jsonl'
            alias = root / 'alias.jsonl'
            records = [dict(transfer_id=7, payload_bytes=32)]
            reference.write_text(json.dumps(records[0]) + '\n')
            alias.hardlink_to(reference)
            with patch('moe_ub_cosim.research_export.json_lines', side_effect=AssertionError('duplicate parse')):
                require_same_records(reference, alias, records, 'port mismatch')
            alias.unlink()
            alias.write_text(json.dumps(records[0], separators=(',', ':')) + '\n')
            require_same_records(reference, alias, records, 'port mismatch')
            alias.write_text(json.dumps(dict(transfer_id=7, payload_bytes=31)) + '\n')
            with self.assertRaisesRegex(ValueError, 'port mismatch'):
                require_same_records(reference, alias, records, 'port mismatch')
            alias.unlink()
            with self.assertRaises(FileNotFoundError):
                require_same_records(reference, alias, records, 'port mismatch')


if __name__ == '__main__':
    unittest.main()
