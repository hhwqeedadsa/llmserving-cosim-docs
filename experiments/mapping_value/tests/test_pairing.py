"""Reject hidden changes in paired runs; exercise the storage accounting boundary."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.mapping_value.common import save
from experiments.mapping_value.full import storage_usage
from experiments.mapping_value.pairing import check_pair


class PairedInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.normal=self.root/'normal';self.case=self.root/'case'
        layer=dict(layer_id=2,rank_ids=[0],endpoint_prepare=dict(ranks=[dict(rank_id=0,
            dispatch_ns_per_local_token=0,combine_ns_per_expert_input=0)]))
        self.deployment=dict(groups=[dict(group_id='ep0',layer_configs=[layer],model_spec={'hidden_size':16})])
        self.declaration=dict(endpoint_prepare=[],port_service=[])
        for run in (self.normal,self.case):
            save(run/'manifest.json',dict(backend_binary={'sha256':'same'},software={'source_hashes':{'platform':'same'}}))
            save(run/'raw/deployment.json',self.deployment)
            save(run/'inputs/network/backend.json',dict(attributes={'workers':1},origins={},port_service={'intervals':[]}))
            for name in ('system.json','network/node.csv','network/topology.csv','network/routing_table.csv','network/transport_channel.csv'):
                path=run/'inputs'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('identical\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_undeclared_endpoint_change_rejected(self):
        value=copy.deepcopy(self.deployment)
        value['groups'][0]['layer_configs'][0]['endpoint_prepare']['ranks'][0]['dispatch_ns_per_local_token']=10
        save(self.case/'raw/deployment.json',value)
        with self.assertRaisesRegex(ValueError,'endpoint differences'):
            check_pair(self.case,self.normal,self.declaration)

    def test_undeclared_backend_change_rejected(self):
        save(self.case/'inputs/network/backend.json',dict(attributes={'workers':2},origins={},port_service={'intervals':[]}))
        with self.assertRaisesRegex(ValueError,'backend parameters'):
            check_pair(self.case,self.normal,self.declaration)

    def test_route_change_rejected_even_with_same_call_ids(self):
        with patch('experiments.mapping_value.pairing.structural_transfers',side_effect=[(1,'a'),(1,'b')]):
            with self.assertRaisesRegex(ValueError,'transfer structure'):
                check_pair(self.case,self.normal,self.declaration)

    def test_hardlinked_reference_is_charged_once(self):
        import os
        first=self.root/'one';second=self.root/'two';first.mkdir();second.mkdir()
        data=first/'data';data.write_bytes(b'x'*8192);os.link(data,second/'shared')
        usage=storage_usage([first,second,first])
        self.assertEqual(usage['apparent_bytes'],8192)
        self.assertEqual(usage['unique_files'],1)


if __name__=='__main__':
    unittest.main()
