"""Context lifetime/alias/NIC boundaries, independent of transport execution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from moe_ub_cosim.context_transactions import compile_program,execute

ROOT=Path(__file__).resolve().parents[2]


class ContextBindingTests(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((ROOT/'examples/scaleout_context_transactions/program.json').read_text())
        self.p=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.t=dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8+i,kind='tor',plane=i) for i in range(2)],
            links=[dict(src=i,dst=8+p,src_port=1+p,dst_port=i+1,rate_bps=100000000000,delay_ns=1000)
                   for p in range(2) for i in range(8)])

    def test_reuse_identity_and_isolate_contexts_and_communicators(self):
        before=deepcopy(self.c);program,m=compile_program(self.t,self.p,self.c)
        self.assertEqual(self.c,before)
        self.assertEqual([o.get('connection') for o in program['operations']],[0,1,2,3,None,0,0])
        self.assertEqual([r['allocated_contexts'] for r in m['bindings']],[4]*4)
        self.assertEqual([(c['source_nic'],c['destination_nic'],c['plane']) for c in program['connections']],[(1,1,0),(2,2,1),(1,1,0),(1,1,0)])
        self.assertEqual([(r['gin_connection'],r['local_context']) for r in m['bindings']],[(0,0),(1,0),(0,1),(0,0)])

    def test_multiple_connections_may_share_one_physical_nic(self):
        c=deepcopy(self.c)
        for comm in c['communicators']:
            for rank in comm['ranks']:rank['nics']=[1]
        c['bindings'][1]['plane']=0
        p,m=compile_program(self.t,self.p,c)
        self.assertEqual([x['source_nic'] for x in p['connections']],[1]*4)
        self.assertEqual(m['bindings'][1]['gin_connection'],1)

    def test_rounding_allocates_context_above_requested_hint(self):
        c=deepcopy(self.c);c['bindings'][1]['context_index']=3
        _,m=compile_program(self.t,self.p,c)
        self.assertEqual((m['bindings'][1]['gin_connection'],m['bindings'][1]['local_context']),(1,1))

    def test_duplicate_identity_cannot_hide_behind_another_binding_name(self):
        c=deepcopy(self.c);c['bindings'][2]['context_index']=0
        with self.assertRaisesRegex(ValueError,'duplicate persistent'):compile_program(self.t,self.p,c)

    def test_self_peer_is_rejected_even_when_unused(self):
        c=deepcopy(self.c);c['bindings'][0]['peer_rank']=0
        with self.assertRaisesRegex(ValueError,'self-peer'):compile_program(self.t,self.p,c)

    def test_unknown_or_numeric_operation_binding_is_rejected(self):
        for value in ('missing',[],True,None):
            c=deepcopy(self.c);c['program']['operations'][0]['binding']=value
            with self.subTest(value=value),self.assertRaises(ValueError):compile_program(self.t,self.p,c)
        c=deepcopy(self.c);c['program']['operations'][0]['connection']=0
        with self.assertRaises(ValueError):compile_program(self.t,self.p,c)

    def test_wrong_node_nic_plane_or_port_is_rejected(self):
        changes=[('node',8),('node',31),('nics',[3]),('nics',[False])]
        for field,value in changes:
            c=deepcopy(self.c);c['communicators'][0]['ranks'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):compile_program(self.t,self.p,c)
        for field,value in [('plane',1),('request_port',24001),('queue_depth',3),('context_index',4)]:
            c=deepcopy(self.c);c['bindings'][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):compile_program(self.t,self.p,c)

    def test_types_names_and_unknown_fields_are_checked(self):
        for section,field,value in [('communicators','requested_contexts',True),('communicators','connections',0),
                                    ('bindings','context_index',True),('bindings','src_rank',False),('bindings','name','../x')]:
            c=deepcopy(self.c);c[section][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):compile_program(self.t,self.p,c)
        c=deepcopy(self.c);c['bindings'][0]['source_nic']=1
        with self.assertRaises(ValueError):compile_program(self.t,self.p,c)

    def test_existing_address_and_effect_order_guards_are_kept(self):
        for address in (4097,8192):
            c=deepcopy(self.c);c['program']['operations'][5]['address']=address
            with self.subTest(address=address),self.assertRaises(ValueError):compile_program(self.t,self.p,c)
        c=deepcopy(self.c);c['program']['operations'][5]['after']=[]
        with self.assertRaises(ValueError):compile_program(self.t,self.p,c)

    def test_invalid_input_creates_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);top=root/'top';top.mkdir()
            (top/'topology.json').write_text(json.dumps(self.t));(root/'protocol.json').write_text(json.dumps(self.p))
            c=deepcopy(self.c);c['bindings'][0]['peer_rank']=0
            (root/'input.json').write_text(json.dumps(c))
            with self.assertRaises(ValueError):execute(root/'input.json',top,root/'protocol.json',root/'out')
            self.assertFalse((root/'out').exists())


if __name__=='__main__':unittest.main()
