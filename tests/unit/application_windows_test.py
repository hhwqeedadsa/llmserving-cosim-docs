"""Application use and close admission; completion is stronger than prepared/applied."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_topology import generate
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


class ApplicationWindowsTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'examples/scaleout_rdma_transactions'
        self.work=json.loads((base/'application-windows.json').read_text())
        self.protocol=json.loads((base/'nic-registration-rdma.json').read_text())
        self.topology=generate(json.loads((base/'nic-registration-topology.json').read_text()))[0]

    def check(self,w):return validate(self.topology,self.protocol,w)

    def test_valid_and_sparse(self):
        self.check(self.work)
        w=deepcopy(self.work);w['schema_version']='moe-rdma-transactions/5'
        for v in w['windows']:
            raw=v.pop('hex');v.update(bytes=65536,initialization='zero_fill/1',segments=[dict(offset=0,hex=raw)])
        self.check(w)

    def test_invalid_declarations(self):
        for field,value in [('id',True),('owner',False),('window_key',99),('address',8191),('bytes',65),('extra',0)]:
            w=deepcopy(self.work);w['application_windows'][1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):self.check(w)
        for field in ('application_window_model','application_windows','registration_model'):
            w=deepcopy(self.work);del w[field]
            with self.assertRaises(ValueError):self.check(w)

    def test_foreign_closed_missing_and_empty_uses(self):
        for index,ids in [(1,[10]),(1,[1]),(1,[]),(1,[11,11]),(1,[True]),(1,[99]),(3,[11])]:
            w=deepcopy(self.work);w['operations'][index]['application_uses']=ids
            with self.subTest(index=index,ids=ids),self.assertRaises(ValueError):self.check(w)
        w=deepcopy(self.work);del w['operations'][3]['application_uses']
        with self.assertRaises(ValueError):self.check(w)

    def test_subrange_must_cover_copy_and_wait(self):
        w=deepcopy(self.work);w['application_windows'][2]['bytes']=8
        with self.assertRaises(ValueError):self.check(w)
        w=deepcopy(self.work);w['operations'][1]['local_wait']=[dict(key=9,address=8248,compare='eq',value=0)]
        self.check(w);w['application_windows'][2]['bytes']=40
        with self.assertRaises(ValueError):self.check(w)

    def test_close_requires_completion_and_owned_reference(self):
        w=deepcopy(self.work);w['operations'][2]['after']=[]
        with self.assertRaises(ValueError):self.check(w)
        for event in ('applied','request_acked'):
            w=deepcopy(self.work);w['operations'][4]['after'][0]['event']=event
            with self.assertRaises(ValueError):self.check(w)
        for field,value in [('rank',0),('release_window',10),('application_uses',[11])]:
            w=deepcopy(self.work);w['operations'][2][field]=value
            with self.assertRaises(ValueError):self.check(w)

    def test_transitive_completion_is_accepted_but_prepared_is_not(self):
        w=deepcopy(self.work)
        w['operations'].insert(4,dict(id=8,kind='local',rank=0,duration_ns=1,after=[dict(operation=3,event='completed')]))
        w['operations'][-1]['after']=[dict(operation=8,event='completed')];self.check(w)
        w['schema_version']='moe-rdma-transactions/5';w['submission_model']=dict(mode='explicit_prefix/1')
        for v in w['windows']:
            raw=v.pop('hex');v.update(bytes=64,initialization='zero_fill/1',segments=[dict(offset=0,hex=raw)])
        w['operations'][3]['aggregate_requests']=False
        w['operations'][4]['after'][0]['event']='prepared'
        with self.assertRaises(ValueError):self.check(w)

    def test_online_full_candidate_rejects_without_mutation(self):
        w=deepcopy(self.work);w['operations']=w['operations'][:3]
        self.check_allow_empty_remote(w)
        before=deepcopy(w);candidate=deepcopy(w)
        candidate['operations'].append(dict(id=5,kind='local',rank=4,duration_ns=1,application_uses=[11],after=[]))
        with self.assertRaises(ValueError):self.check_allow_empty_remote(candidate)
        self.assertEqual(w,before)

    def check_allow_empty_remote(self,w):return validate(self.topology,self.protocol,w,allow_no_remote=True)


if __name__=='__main__':unittest.main()
