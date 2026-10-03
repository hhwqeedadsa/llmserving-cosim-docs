"""Independent contribution/byte and completion checks for the pinned CANN path."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan
from moe_ub_cosim.composed_service import ComposedService

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    return json.loads((ROOT/'fixtures/m10_cann/layer.json').read_text())


class CannPlanTest(unittest.TestCase):
    def test_separate_experts_and_true_copy_length(self):
        c=fixture();c['tokens']=[dict(c['tokens'][0],src_rank=2,expert_ids=[4,5])]
        p=build_plan(c)
        data=[t for t in p['transfers'] if t['message_role']=='data']
        self.assertEqual(sum(t['business_payload_bytes'] for t in data),2*(1025+2050))
        dispatch=[t for t in data if t['kind']=='dispatch']
        self.assertEqual(sum(t['payload_bytes'] for t in dispatch),2*(1056+44))
        self.assertEqual(sum(t['application_metadata_bytes'] for t in dispatch),32)
        self.assertEqual(sum(t['padding_bytes'] for t in dispatch),118)
        self.assertEqual({tuple(t['sources'][0]['expert_ids']) for t in dispatch},{(4,),(5,)})
        self.assertEqual(p['communication_buffers']['dispatch_window_stride'],1536)
        self.assertEqual(p['expert_input_counts'],[0,0,2])

    def test_zero_counts_and_three_localities(self):
        c=fixture();c['tokens']=[dict(c['tokens'][0],src_rank=2,expert_ids=[0,2])]
        p=build_plan(c)
        self.assertEqual([x['locality'] for x in p['contributions']],['same_rank','same_chip'])
        self.assertFalse(any(t['message_role']=='data' and 'ub_task_id' in t for t in p['transfers']))
        states=[t for t in p['transfers'] if t['kind']=='dispatch' and t['message_role']=='status']
        self.assertEqual(len(states),3*(6-2))
        self.assertEqual(sum(t['token_count'] for t in states),1)
        self.assertTrue(all(t['business_payload_bytes']==0 and t['payload_bytes']==32 for t in states))
        self.assertEqual(sum(t['payload_bytes'] for t in states if 'ub_task_id' in t),8*32)

    def test_publication_waits_for_data_and_reorder(self):
        c=fixture();c['tokens']=[dict(c['tokens'][0],src_rank=2,expert_ids=[4,5])]
        p=build_plan(c);graphs={g['rank_id']:g for g in p['rank_graphs']}
        for rank,g in graphs.items():
            nodes={n['node_id']:n for n in g['nodes']}
            def ancestors(i):
                deps=set(nodes[i]['deps'])
                for d in list(deps):deps.update(ancestors(d))
                return deps
            data={t['send_node_id'] for t in p['transfers'] if t['src_rank']==rank and t['kind']=='dispatch' and t['message_role']=='data'}
            for t in p['transfers']:
                if t['src_rank']==rank and t['kind']=='dispatch' and t['message_role']=='status':
                    self.assertTrue(data<=ancestors(t['send_node_id']))
            if g['phase_nodes']['expert']:
                self.assertIn(g['phase_nodes']['dispatch_reorder'],ancestors(g['phase_nodes']['expert']))
        bad=deepcopy(p)
        t=next(t for t in bad['transfers'] if t['src_rank']==2 and t['message_role']=='status')
        next(n for n in bad['rank_graphs'][0]['nodes'] if n['node_id']==t['send_node_id'])['deps']=[]
        with self.assertRaises(ValueError):validate_plan(bad)

    def test_reject_wrong_scope_and_small_buffer(self):
        for key,value in [('algorithm','fullmesh_v2'),('source_revision','main'),('window_bytes',1),('aiv_lanes',0)]:
            c=fixture();c['cann_profile'][key]=value
            with self.assertRaises(ValueError):build_plan(c)
        c=fixture();c['expert_placement']=[2,7,2,7,12,12]
        with self.assertRaisesRegex(ValueError,'contiguous'):build_plan(c)
        c=fixture();c['compute_profile_ns']['expert_local_merge']=1
        with self.assertRaisesRegex(ValueError,'no remote merge'):build_plan(c)

    def test_layout_and_control_tampering(self):
        p=build_plan(fixture())
        for mutate in (lambda t:t.update(business_payload_bytes=t['payload_bytes']),
                       lambda t:t.update(token_count=999),lambda t:t.update(payload_bytes=64)):
            bad=deepcopy(p);t=next(t for t in bad['transfers'] if t['kind']=='dispatch' and t['message_role']=='status')
            mutate(t)
            with self.assertRaises(ValueError):validate_plan(bad)

    def test_composed_capacity_charges_windows_and_preserves_native_pool(self):
        f=ROOT/'fixtures/m10_cann'
        c=json.loads((f/'service.json').read_text());l=json.loads((f/'layers.json').read_text())
        service=ComposedService(c,l)
        self.assertTrue(all(x['free_blocks']==8 for x in service.memory_snapshot()))
        c['groups'][0]['memory']['ranks'][0]['communication_buffer_bytes']=1024
        with self.assertRaisesRegex(ValueError,'Communication buffer'):ComposedService(c,l)


if __name__=='__main__':unittest.main()
