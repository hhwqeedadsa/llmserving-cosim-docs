from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_streams import stream_limits,validate

ROOT=Path(__file__).resolve().parents[2]


class PersistentScaleTests(unittest.TestCase):
    def setUp(self):
        self.w=json.loads((ROOT/'examples/scaleout_rdma_streams/local-stages.json').read_text())
        self.w['schema_version']='moe-rdma-stream-work/4'
        self.w['limits']=dict(max_streams=4096,max_work=65536,max_link_transmissions=2000000)
        self.protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology=dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0)],
            links=[dict(src=i,dst=8,src_port=1,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_explicit_scale_bounds(self):
        validate(self.topology,self.protocol,self.w)
        self.assertEqual(stream_limits(self.w),self.w['limits'])
        c=deepcopy(self.w)
        c['streams']=[dict(c['streams'][0],id=i,wire_port=21000+i) for i in range(129)]
        validate(self.topology,self.protocol,c)

    def test_old_schemas_do_not_gain_counts(self):
        for schema in ('2','3'):
            for key,value in (('max_streams',129),('max_work',8193)):
                c=deepcopy(self.w);c['schema_version']='moe-rdma-stream-work/'+schema
                if schema=='2':del c['local_model']
                del c['limits']['max_link_transmissions'];c['limits'][key]=value
                with self.subTest(schema=schema,key=key),self.assertRaises(ValueError):stream_limits(c)

    def test_boundaries_types_and_required_fields(self):
        for key,upper in [('max_streams',4096),('max_work',65536),('max_link_transmissions',2000000)]:
            for value in (0,-1,True,1.0,str(upper),upper+1):
                c=deepcopy(self.w);c['limits'][key]=value
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):stream_limits(c)
            c=deepcopy(self.w);del c['limits'][key]
            with self.assertRaises(ValueError):stream_limits(c)
        c=deepcopy(self.w);c['limits']['extra']=1
        with self.assertRaises(ValueError):stream_limits(c)

    def test_dependencies_and_endpoint_safety_preserved(self):
        for mode in ('future','nic','local_kind','work_budget'):
            c=deepcopy(self.w)
            if mode=='future':c['work'][0]['after']=[dict(work=65535,event='acked')]
            if mode=='nic':c['streams'][0]['source_nic']=999
            if mode=='local_kind':c['work'][1]['after'][0]['event']='completed'
            if mode=='work_budget':c['limits']['max_work']=1
            with self.subTest(mode=mode),self.assertRaises(ValueError):validate(self.topology,self.protocol,c)


if __name__=='__main__':unittest.main()
