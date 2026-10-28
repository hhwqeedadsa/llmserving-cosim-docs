"""Input isolation and bounds for concurrent operation-packet reference."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from examples.rc_concurrent.prepare import prepare
from moe_ub_cosim.rc_concurrent import validate


class ConcurrentInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        root=prepare(Path(cls.temp.name)/'inputs')
        cls.topology=json.loads((root/'topology/topology.json').read_bytes())
        cls.protocol=json.loads((root/'rdma.json').read_bytes())
        cls.work=json.loads((root/'healthy.json').read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def rejected(self,change):
        work=deepcopy(self.work);change(work)
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,work)

    def test_shared_target_and_same_endpoint_distinct_qps(self):
        result=validate(self.topology,self.protocol,self.work)
        self.assertEqual((result['connections'],result['operations'],result['request_packets'],result['write_bytes']),
                         (4,8,14,1832))

    def test_duplicate_identity(self):
        self.rejected(lambda w:w['connections'][2].update(id=0))

    def test_bool_identity(self):
        self.rejected(lambda w:w['connections'][0].update(id=False))

    def test_loss_is_not_matched_by_psn_alone(self):
        self.rejected(lambda w:w.update(loss=dict(connection=3,node=0,opcode=18,psn=3)))

    def test_valid_atomic_feedback_loss(self):
        work=deepcopy(self.work);work['loss']=dict(connection=1,node=1,opcode=18,psn=3)
        self.assertEqual(validate(self.topology,self.protocol,work)['connections'],4)

    def test_missing_endpoint_memory(self):
        self.rejected(lambda w:w['memories'].pop())

    def test_unused_memory_still_has_to_be_in_topology(self):
        self.rejected(lambda w:w['memories'].append(dict(gpu=4,data_hex='00'*8)))

    def test_missing_nic_binding(self):
        self.rejected(lambda w:w['connections'][0].update(source_nic=2))

    def test_cross_connection_byte_budget(self):
        def change(work):
            for q in work['connections']:q['operations']=[dict(kind='write',address=0,data_hex='00'*4096)]*5
        self.rejected(change)

    def test_cross_connection_operation_budget(self):
        self.rejected(lambda w:[q.update(operations=[dict(kind='fetch_add',address=0,addend=1)]*17) for q in w['connections']])

    def test_atomic_alignment(self):
        self.rejected(lambda w:w['connections'][0]['operations'][1].update(address=3073))

    def test_old_probe_schema_is_distinct(self):
        self.rejected(lambda w:w.update(schema_version='moe-rc-packet-probe/1'))


if __name__=='__main__':unittest.main()
