"""NIC aliases must share physical ranges and preserve remote effect ordering."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


class MemoryRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.work=json.loads((ROOT/'examples/scaleout_rdma_transactions/nic-registration.json').read_text())
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=generate(json.loads((ROOT/'examples/scaleout_rdma_transactions/nic-registration-topology.json').read_text()))[0]

    def test_valid_two_nic_shared_storage(self):
        validate(self.topology,self.protocol,self.work)

    def test_registration_fields_pairing_and_scope(self):
        bad=[]
        for name in ('registration_model','registrations'):
            w=deepcopy(self.work);del w[name];bad.append(w)
        for name,value in [('id',True),('nic',0),('nic',99),('key',0),('window_key',99),('address',8191),
                           ('bytes',0),('bytes',65),('allow_atomic',1),('unknown',0)]:
            w=deepcopy(self.work);w['registrations'][0][name]=value;bad.append(w)
        w=deepcopy(self.work);w['registrations'].append(deepcopy(w['registrations'][0]));bad.append(w)
        w=deepcopy(self.work);w['registrations'][2]['id']=1;bad.append(w)
        w=deepcopy(self.work);w['windows'][1]['allow_atomic']=False;bad.append(w)
        for w in bad:
            with self.subTest(w=w['registrations'] if 'registrations' in w else 'missing'):
                with self.assertRaises(ValueError):validate(self.topology,self.protocol,w)

    def test_target_authority_and_atomic_permissions(self):
        for index,field,value in [(0,'key',22),(1,'address',8210),(2,'key',21)]:
            w=deepcopy(self.work);w['operations'][index][field]=value
            with self.assertRaises(ValueError):validate(self.topology,self.protocol,w)

    def test_aliases_do_not_bypass_write_order(self):
        w=deepcopy(self.work);w['operations'][1]['address']=8192;w['operations'][1]['after']=[]
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,w)
        w['operations'][1]['after']=[dict(operation=0,event='applied')]
        validate(self.topology,self.protocol,w)

    def test_sparse_storage_is_not_multiplied_by_aliases(self):
        w=deepcopy(self.work);w['schema_version']='moe-rdma-transactions/5'
        for storage in w['windows']:
            raw=storage.pop('hex');storage.update(bytes=65536,initialization='zero_fill/1',segments=[dict(offset=0,hex=raw)])
        validate(self.topology,self.protocol,w)


if __name__=='__main__':unittest.main()
