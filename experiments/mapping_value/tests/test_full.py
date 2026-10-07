"""Full case freezing uses the whole layer, not just the anchor's interval."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.mapping_value.common import read, save
from experiments.mapping_value.full import freeze_full, run_full_case


class FullFreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        save(self.root/'preflight.json',dict(source_slice_sha256='source'))
        save(self.root/'replay.json',{})
        save(self.root/'r000/manifest.json',dict(normal_exit=True,source_slice_sha256='source'))
        save(self.root/'configuration/reference.json',dict(target_layer=31,target_port=[768,16]))
        save(self.root/'r000/observed/public_config.json',dict(ports=[dict(switch_id=768,port_id=16,
            period_ns=10,start_local_ns=0,end_local_ns=1000)]))
        self.operations=[dict(layer_id=31,kind='dispatch',endpoint_records=[
            dict(rank_id=0,start_global_ns=23,end_global_ns=40),
            dict(rank_id=1,start_global_ns=17,end_global_ns=55)]),
            dict(layer_id=31,kind='combine',endpoint_records=[
            dict(rank_id=0,start_global_ns=64,end_global_ns=80),
            dict(rank_id=1,start_global_ns=61,end_global_ns=93)])]

    def tearDown(self):
        self.tmp.cleanup()

    def freeze(self):
        with patch('experiments.mapping_value.full.stored_rows',return_value=iter(self.operations)):
            return freeze_full(self.root,self.root/'frozen')

    def test_all_participants_and_both_calls_determine_interval(self):
        self.assertEqual(self.freeze()['interval_ns'],[10,100])
        self.assertEqual(read(self.root/'frozen/r001.json')['port_service'][0]['rate_bps'],112_000_000_000)
        self.assertEqual(read(self.root/'frozen/r002.json')['port_service'][0]['rate_bps'],56_000_000_000)
        self.assertEqual(read(self.root/'frozen/r000.json')['port_service'],[])

    def test_missing_capture_is_rejected(self):
        save(self.root/'r000/observed/public_config.json',dict(ports=[]))
        with self.assertRaisesRegex(ValueError,'must be observed'):
            self.freeze()
        self.assertFalse((self.root/'frozen').exists())

    def test_changed_frozen_case_is_rejected_before_launch(self):
        self.freeze(); path=self.root/'frozen/r001.json'; case=read(path)
        case['port_service'][0]['rate_bps']=1; save(path,case)
        with self.assertRaisesRegex(ValueError,'frozen declaration'):
            run_full_case(self.root,path,self.root/'treatment')
        self.assertFalse((self.root/'treatment').exists())


if __name__=='__main__':
    unittest.main()
