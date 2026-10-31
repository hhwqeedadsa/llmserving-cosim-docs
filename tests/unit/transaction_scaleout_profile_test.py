"""Explicit expanded software limits, including old-input and safety boundaries."""
from copy import deepcopy
import json
import io
from unittest.mock import Mock
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate, execution_limits
from moe_ub_cosim.transaction_capacity import capacity, host_budget
from moe_ub_cosim.scaleout_topology import generate

ROOT=Path(__file__).resolve().parents[2]


class ScaleOutProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work=json.loads((ROOT/'examples/scaleout_rdma_transactions/scaleout-profile.json').read_text())
        cls.protocol=json.loads((ROOT/'examples/scaleout_clos128/rdma.json').read_text())
        cls.protocol['payload_bytes']=1024
        cls.topology,_=generate(json.loads((ROOT/'examples/scaleout_rdma_transactions/clos128-network.json').read_text()))

    def test_full_artificial_admission(self):
        validate(self.topology,self.protocol,self.work)
        self.assertEqual(len(self.work['windows']),96)
        self.assertEqual(len(self.work['registrations']),64)
        self.assertEqual(len(self.work['connections']),65)

    def test_counts_profile_types_and_unknown_fields(self):
        for key,lo,hi in [('max_connections',1,2048),('max_remote_operations',1,16384),('max_local_operations',0,1024)]:
            for n in [lo,hi]:
                good=deepcopy(self.work);good['execution_limits'][key]=n
                self.assertEqual(execution_limits(good)[key],n)
            for n in [lo-1,hi+1,True,False,1.0,'1',None]:
                bad=deepcopy(self.work);bad['execution_limits'][key]=n
                with self.subTest(key=key,n=n),self.assertRaises(ValueError):execution_limits(bad)
        for profile in ['',None,True,{},'scaleout_ep32/2']:
            bad=deepcopy(self.work);bad['execution_limits']['profile']=profile
            with self.assertRaises(ValueError):execution_limits(bad)
        bad=deepcopy(self.work);bad['execution_limits']['unknown']=1
        with self.assertRaises(ValueError):execution_limits(bad)
        bad=deepcopy(self.work);bad['schema_version']='moe-rdma-transactions/4'
        with self.assertRaises(ValueError):execution_limits(bad)

    def test_old_limits_stay_old(self):
        self.assertEqual(capacity({})['windows'],16)
        bad=deepcopy(self.work);del bad['execution_limits']['profile']
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)
        bad['execution_limits']['max_connections']=64
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)

    def test_fixed_expanded_limits_and_topology_membership(self):
        for change in ['window','wait','dependency','owner','absent','registration']:
            bad=deepcopy(self.work)
            if change=='window':
                bad['windows'] += [dict(bad['windows'][0],key=1000+i,address=100000+i*4096) for i in range(33)]
            if change=='wait':bad['operations'][66]['local_wait'].append(dict(key=2,address=8192+400,bits=32,compare='eq',value=0))
            if change=='dependency':bad['operations'][67]['after']*=2
            if change=='owner':bad['windows'][0]['owner']=512
            if change=='absent':
                bad['connections'][0]['src']=511
            if change=='registration':
                bad['registrations'] += [dict(bad['registrations'][0],id=1000+i,key=1000+i) for i in range(193)]
            with self.subTest(change=change),self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)

    def test_safety_not_relaxed_and_no_mutation_on_rejection(self):
        for change in ['permission','range','overlap','source','dependency','queue']:
            bad=deepcopy(self.work)
            if change=='permission':bad['registrations'][3]['allow_atomic']=False
            if change=='range':bad['operations'][0]['address']=999999
            if change=='overlap':bad['operations'][64]['address']=8192
            if change=='source':bad['operations'][0]['source']['key']=99
            if change=='dependency':bad['operations'][67]['after'][0]['operation']=68
            if change=='queue':bad['connections'][0]['queue_depth']=64
            before=deepcopy(bad)
            with self.subTest(change=change),self.assertRaises(ValueError):validate(self.topology,self.protocol,bad)
            self.assertEqual(before,bad)

    def test_session_decision_boundary_without_network(self):
        from moe_ub_cosim.transaction_session import NativeTransactionSession
        for scaled,sequence,allowed in [(False,255,True),(False,256,False),(True,256,True),(True,1023,True),(True,1024,False)]:
            session=NativeTransactionSession.__new__(NativeTransactionSession)
            session.config=deepcopy(self.work) if scaled else dict(operations=[])
            session.pending=None;session.sequence=sequence;session.last_ns=0
            session.p=self.protocol;session._alarms={};session._delivered=set()
            session.logs=io.StringIO();session.transcript=io.StringIO()
            event=dict(type='DECIDE',decision_id=sequence,now_ns=0,events=[],drained=True)
            session._line=Mock(return_value='MOE_TX_EVENT '+json.dumps(event))
            if allowed:self.assertEqual(session.receive(),event)
            else:
                with self.assertRaises(ValueError):session.receive()
                self.assertIsNone(session.pending)

    def test_incremental_identity_cannot_override_fabric(self):
        from moe_ub_cosim.transaction_profile_build import _recorded_sources
        old=dict(sources={'backend/rdma/fabric.hh':'fixed','backend/rdma/persistent_streams.cc':'old'})
        expected=dict(sources={'backend/rdma/persistent_streams.cc':'updated'},recompiled_old_paths=['backend/rdma/persistent_streams.cc'])
        self.assertEqual(_recorded_sources(old,expected)['backend/rdma/fabric.hh'],'fixed')
        expected['recompiled_old_paths'].append('backend/rdma/fabric.hh');expected['sources']['backend/rdma/fabric.hh']='changed'
        with self.assertRaises(ValueError):_recorded_sources(old,expected)
        self.assertEqual(old['sources']['backend/rdma/fabric.hh'],'fixed')

    def test_exact_dependency_boundary(self):
        good=deepcopy(self.work)
        good['execution_limits']['max_local_operations']=132
        good['operations'] += [dict(id=69+i,kind='local',rank=32,duration_ns=0,after=[]) for i in range(128)]
        good['operations'].append(dict(id=197,kind='local',rank=32,duration_ns=0,
            after=[dict(operation=69+i,event='completed') for i in range(128)]))
        validate(self.topology,self.protocol,good)
        good['operations'][-1]['after'].append(dict(operation=68,event='completed'))
        with self.assertRaises(ValueError):validate(self.topology,self.protocol,good)

    def test_host_budgets_are_explicit_and_intersect_protocol(self):
        large=dict(wall_seconds=600,rss_bytes=16*2**30,address_bytes=32*2**30,artifact_bytes=4*2**30,result_json_bytes=16*2**20)
        actual=host_budget(self.work,large)
        self.assertEqual([actual[k] for k in ('wall_seconds','rss_bytes','address_bytes','artifact_bytes')],[180,3*2**30,6*2**30,512*2**20])
        self.assertEqual(host_budget({},large)['wall_seconds'],60)
        small=dict(large,wall_seconds=1,rss_bytes=2**29)
        self.assertEqual(host_budget(self.work,small)['wall_seconds'],1)
        self.assertEqual(host_budget(self.work,small)['rss_bytes'],2**29)


if __name__=='__main__':unittest.main()
