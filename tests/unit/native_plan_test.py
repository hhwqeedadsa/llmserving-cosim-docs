"""Native data ownership, real supply notification and resource preflight."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.plan import build_plan, validate_plan, RunIdentities
from moe_ub_cosim.native import TEMPLATE
from moe_ub_cosim.resources import actual_usage, cann_usage
from moe_ub_cosim.route_admission import CannRouteAdmission

ROOT=Path(__file__).resolve().parents[2]


class NativePlanTest(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT/'fixtures/m11_native/layer.json').read_text())

    def test_real_notification_and_consumer_direction(self):
        c=self.fixture();p=build_plan(c,RunIdentities(90000,70000,80000))
        self.assertEqual(actual_usage(p),cann_usage(c))
        self.assertEqual(CannRouteAdmission(c).check(c['tokens'][0]),cann_usage(c))
        graphs={g['rank_id']:{n['node_id']:n for n in g['nodes']} for g in p['rank_graphs']}
        for t in p['transfers']:
            if t.get('operation')!='LOAD':continue
            self.assertEqual(t['src_rank'],t['consumer_rank'])
            self.assertEqual(t['dst_rank'],t['data_owner_rank'])
            notice=next(x for x in p['transfers'] if x['message_role']=='ready' and
                        x['ready_for_message_id']==t['message_id'] and x['chunk_index']==t['chunk_index'])
            self.assertEqual((notice['src_rank'],notice['dst_rank']),(t['dst_rank'],t['src_rank']))
            self.assertEqual(notice['business_payload_bytes'],0)
            self.assertEqual(notice['application_metadata_bytes'],32)
            self.assertEqual(graphs[t['src_rank']][t['send_node_id']]['deps'],[notice['recv_node_id']])
            self.assertEqual(graphs[t['dst_rank']][t['recv_node_id']]['deps'],[notice['send_node_id']])
        for g in p['rank_graphs']:
            self.assertEqual([n['node_id'] for n in g['nodes']],list(range(1,len(g['nodes'])+1)))
        validate_plan(p)

    def test_source_layout_and_all_operation_combinations(self):
        c=self.fixture();legacy=deepcopy(c);legacy['template']='cann_a3_v850_fullmesh_v1';legacy.pop('native_profile')
        base=build_plan(legacy)
        for dispatch in ('STORE','LOAD'):
            for combine in ('STORE','LOAD'):
                c['native_profile'].update(dispatch=dispatch,combine=combine);p=build_plan(c)
                self.assertEqual(p['contributions'],base['contributions'])
                self.assertEqual(p['communication_buffers'],base['communication_buffers'])
                self.assertEqual(sum(t['business_payload_bytes'] for t in p['transfers']),
                                 sum(t['business_payload_bytes'] for t in base['transfers']))
                self.assertEqual(actual_usage(p),cann_usage(c))

    def test_cannot_drop_notification_or_change_ownership(self):
        p=build_plan(self.fixture())
        for mutation in ('drop_notice','wrong_peer','early_load','wrong_bytes'):
            bad=deepcopy(p);load=next(t for t in bad['transfers'] if t.get('operation')=='LOAD')
            if mutation=='drop_notice':
                bad['transfers']=[t for t in bad['transfers'] if t['message_role']!='ready']
            elif mutation=='wrong_peer':load['data_owner_rank']=load['src_rank']
            elif mutation=='wrong_bytes':load['payload_bytes']+=1
            else:
                node=next(n for g in bad['rank_graphs'] if g['rank_id']==load['src_rank'] for n in g['nodes'] if n['node_id']==load['send_node_id'])
                node['deps']=[]
            with self.assertRaises(ValueError):validate_plan(bad)

    def test_public_mapping_does_not_mutate_or_alias_input(self):
        from moe_ub_cosim.native import apply_mapping
        c=self.fixture();legacy=deepcopy(c);legacy['template']='cann_a3_v850_fullmesh_v1';legacy.pop('native_profile')
        base=build_plan(legacy);before=deepcopy(base);mapped=apply_mapping(base,c)
        self.assertEqual(base,before)
        mapped['rank_graphs'][0]['nodes'][0]['deps'].append(999)
        mapped['transfers'][0]['payload_regions'][0]['end']+=1
        self.assertEqual(base,before)

    def test_layout_cost_and_status_dependencies_still_validated(self):
        p=build_plan(self.fixture())
        for mutation in ('cost','region','status_dependency','source'):
            bad=deepcopy(p)
            if mutation=='cost':
                next(n for g in bad['rank_graphs'] for n in g['nodes'] if n['kind']=='COMP')['duration_ns']+=1
            elif mutation=='region':bad['transfers'][0]['payload_regions'][0]['end']+=1
            elif mutation=='source':next(t for t in bad['transfers'] if t['sources'])['sources'].clear()
            else:
                tid=next(t['transfer_id'] for t in bad['transfers'] if t['message_role']=='status')
                next(n for g in bad['rank_graphs'] for n in g['nodes'] if n.get('transfer_id')==tid and n['deps'])['deps'].clear()
            with self.assertRaises(ValueError):validate_plan(bad)


if __name__=='__main__':unittest.main()
