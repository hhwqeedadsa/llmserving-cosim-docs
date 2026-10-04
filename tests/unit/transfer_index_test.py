import tempfile
from pathlib import Path
import unittest

from moe_ub_cosim.transfer_index import TransferIndex,ROW


def transfer(tid,task):
    return dict(transfer_id=tid,ub_task_id=task,operation_id='op'+str(tid%2),payload_bytes=32,
        payload_regions=[dict(begin=0,end=16,kind='business'),dict(begin=16,end=32,kind='metadata')])


class TransferIndexTest(unittest.TestCase):
    def index(self,path,**limits):
        return TransferIndex(path,**dict(dict(max_bytes=ROW.size*100,max_operations=10,max_layouts=10,max_id_ranges=10,cache_rows=2),**limits))

    def test_exact_sparse_id_lookup_and_write_conservation(self):
        with tempfile.TemporaryDirectory() as tmp,self.index(Path(tmp)/'index') as index:
            values=[transfer(2**40+v,task) for v,task in [(1,4),(2,5),(9,9),(10,10)]]
            for value in values:index.add(value)
            index.seal()
            for value in values*2:
                self.assertEqual(index.get(value['transfer_id']),value)
            self.assertLessEqual(len(index.cache),2);self.assertEqual(len(index.layouts),1)
            self.assertIsNone(index.get(2**40+3));self.assertIsNone(index.get(2**40+11))
            index.add_write(9,12);index.add_write(9,20);self.assertEqual(index.delivered(9),32)
            with self.assertRaisesRegex(ValueError,'Duplicate'):index.add_write(9,1)
            with self.assertRaisesRegex(ValueError,'Unknown'):index.add_write(8,1)
            self.assertEqual(index.delivered(4),0)
            self.assertEqual(index.path.stat().st_size,4*ROW.size)

    def test_finite_limits_and_empty_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            with self.index(path/'empty') as index:
                index.seal();self.assertIsNone(index.get(1))
                with self.assertRaisesRegex(ValueError,'Unknown'):index.add_write(0,1)
            with self.index(path/'limit',max_bytes=ROW.size) as index:
                index.add(transfer(100,0))
                with self.assertRaisesRegex(ValueError,'disk budget'):index.add(transfer(101,1))
            for kind in ('max_operations','max_id_ranges'):
                with self.index(path/kind,**{kind:1}) as index:
                    index.add(transfer(100,0))
                    with self.assertRaisesRegex(ValueError,'budget'):index.add(transfer(101,2))

    def test_transfer_gaps_with_contiguous_tasks_and_one_mapped_read(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp,self.index(Path(tmp)/'index') as index:
            values=[transfer(tid,task) for task,tid in enumerate([3,4,5,100,101,1000,1001])]
            for value in values:index.add(value)
            index.seal();self.assertEqual(len(index.ranges),1)
            self.assertEqual(len(index.transfer_starts),3)
            with patch.object(index,'_row',wraps=index._row) as read:
                for value in values:
                    before=read.call_count;self.assertEqual(index.get(value['transfer_id']),value)
                    self.assertEqual(read.call_count-before,1)
                for tid in [1,2,6,7,99,102,998,999,1002,2**64-1]:self.assertIsNone(index.get(tid))


if __name__=='__main__':unittest.main()
