"""Reject unsafe RC inputs before network allocation; exercise bounded codec."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from moe_ub_cosim.rc_packet import validate

ROOT=Path(__file__).resolve().parents[2]


class RcPacketInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec=importlib.util.spec_from_file_location('rc_example',ROOT/'examples/rc_packet_probe/prepare.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        cls.tmp=tempfile.TemporaryDirectory()
        cls.directory=module.prepare(Path(cls.tmp.name)/'input')
        cls.t=json.loads((cls.directory/'topology/topology.json').read_bytes())
        cls.p=json.loads((cls.directory/'rdma.json').read_bytes())
        cls.w=json.loads((cls.directory/'healthy.json').read_bytes())

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def test_declared_four_cases(self):
        for name in ('healthy','drop_write_middle','drop_write_ack','drop_atomic_ack'):
            self.assertEqual(validate(self.t,self.p,json.loads((self.directory/(name+'.json')).read_bytes())),
                             dict(operations=2,request_packets=4,write_bytes=600))

    def test_reject_unsafe_work(self):
        cases=[]
        for key,value in [('mtu',True),('mtu',255),('rto_ns',0),('memory_hex','FF'),('operations',[]),
                ('operations',[self.w['operations'][0]]*65),('src',True),('loss',dict(node=0,opcode=18,psn=2))]:
            c=copy.deepcopy(self.w);c[key]=value;cases.append(c)
        for op in [dict(kind='fetch_add',address=769,addend=5),dict(kind='fetch_add',address=1024,addend=5),
                dict(kind='fetch_add',address=768,addend=-1),dict(kind='write',address=900,data_hex='ff'*600),
                dict(kind='write',address=0,data_hex=''),dict(kind='read',address=0,data_hex='00')]:
            c=copy.deepcopy(self.w);c['operations']=[op];cases.append(c)
        for i,c in enumerate(cases):
            with self.subTest(i=i),self.assertRaises(ValueError):validate(self.t,self.p,c)

    def test_reject_legacy_protocol_mix(self):
        for key in ('faults','drop_once','recovery','admission','link_failures','local_failover'):
            p=copy.deepcopy(self.p);p[key]={}
            with self.subTest(key=key),self.assertRaises(ValueError):validate(self.t,p,self.w)


if __name__=='__main__':unittest.main()
