import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.observation import public_config
from moe_ub_cosim.research_storage import ResearchWriter,logical_windows,validate_observed_storage


class ResearchStorageTest(unittest.TestCase):
    def build(self,root):
        writer=ResearchWriter(root,100000,10000)
        writer.open('observed','endpoint_ops.jsonl');writer.open('observed','port_windows.csv')
        port=dict(switch_id=8,port_id=1,direction='egress',start_local_ns=-10,end_local_ns=25,period_ns=10)
        (root/'observed/public_config.json').write_text(json.dumps(public_config([2],[port])))
        writer.add('observed','endpoint_ops.jsonl',dict(rank_id=2,local_record_id=1,kind='dispatch',start_local_ns=-7,end_local_ns=5))
        writer.add('observed','port_windows.csv',dict(switch_id=8,port_id=1,direction='egress',
            local_window_start_ns=0,local_window_end_ns=10,counted_packets=2,counted_packet_bytes=70))
        writer.finish(4);return root/'observed'

    def test_lossless_zeros_and_observed_only_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            observed=self.build(Path(tmp)/'output')
            result=validate_observed_storage(observed)
            self.assertEqual((result['port_windows'],result['zero_windows'],result['endpoint_records']),(4,3,1))
            rows=list(logical_windows(observed))
            self.assertEqual([r['counted_packets'] for r in rows],[0,2,0,0])
            self.assertEqual(rows[-1]['local_window_end_ns'],25)
            (observed/'truth.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'unexpected'):validate_observed_storage(observed)

    def test_missing_interval_declaration_corruption_and_budget_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);observed=self.build(root/'output')
            path=observed/'storage.json';metadata=json.loads(path.read_text());metadata['logical_windows']=3
            path.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError,'window declaration'):list(logical_windows(observed))
            metadata['logical_windows']=4;path.write_text(json.dumps(metadata))
            data=observed/'records/port_windows.csv.gz';data.write_bytes(data.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError,'size mismatch'):validate_observed_storage(observed)
            writer=ResearchWriter(root/'limit',1,100)
            with self.assertRaisesRegex(ValueError,'disk budget'):writer.open('observed','endpoint_ops.jsonl')
            writer.close();self.assertFalse((root/'limit/observed/storage.json').exists())


if __name__=='__main__':unittest.main()
