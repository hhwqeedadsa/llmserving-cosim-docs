"""Independent invariants for source cores, bounded buffers and partial readiness."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.a3 import resource_description, usage
from moe_ub_cosim.plan import build_plan, validate_plan, RunIdentities
from moe_ub_cosim.resources import actual_usage

ROOT=Path(__file__).resolve().parents[2]


def fixture():
    return json.loads((ROOT/'fixtures/m19_a3_resources/layer.json').read_text())


class A3PlanTests(unittest.TestCase):
    def test_layout_work_and_preflight(self):
        for fence in ('data_ready','local_read_complete'):
            c=fixture();c['cann_profile']['copy_fence']=fence
            p=build_plan(c,RunIdentities(transfer=10000,task=500,tag=400))
            self.assertEqual(actual_usage(p),usage(c))
            self.assertEqual([g['compute_work_ns'] for g in p['rank_graphs']],[610,60,210])
            self.assertEqual(sum(t['business_payload_bytes'] for t in p['transfers']),1025+2050)
            self.assertEqual(sum(t['kind']=='dispatch' and t['message_role']=='status' for t in p['transfers']),12)
            self.assertEqual({t['operation'] for t in p['transfers'] if 'ub_task_id' in t},{'STORE'})
            self.assertEqual(p['send_slots_per_rank'],0)
            validate_plan(p)

    def test_independent_token_reduction_and_operator_boundary(self):
        c=fixture();c['tokens']=[dict(c['tokens'][0],request_id=f't{i}',expert_ids=[4,5]) for i in range(2)]
        p=build_plan(c);g=p['rank_graphs'][0];nodes={n['node_id']:n for n in g['nodes']}
        def ancestors(node):
            found=set();pending=list(nodes[node]['deps'])
            while pending:
                d=pending.pop()
                if d not in found:found.add(d);pending.extend(nodes[d]['deps'])
            return found
        a,b=g['phase_members']['reduce']
        received={i:{t['recv_node_id'] for t in p['transfers'] if t['dst_rank']==2 and t['kind']=='combine' and t['sources'][0]['source_record_index']==i} for i in range(2)}
        self.assertTrue(received[0]<=ancestors(a));self.assertTrue(received[1]<=ancestors(b))
        self.assertFalse(received[1]&ancestors(a));self.assertFalse(received[0]&ancestors(b))
        self.assertEqual(nodes[a]['resource_id'],'aiv:0');self.assertEqual(nodes[b]['resource_id'],'aiv:1')
        self.assertTrue({a,b}<=ancestors(g['phase_nodes']['finish']))

    def test_same_buffer_reuse_waits_without_previous_transaction(self):
        c=fixture();c['cann_profile']['platform_aiv_cores']=1
        c['tokens']=[dict(c['tokens'][0],request_id=f't{i}',expert_ids=[4,5]) for i in range(2)]
        p=build_plan(c);g=p['rank_graphs'][0];nodes={n['node_id']:n for n in g['nodes']}
        prepares=g['phase_members']['prepare'];third=nodes[prepares[2]]
        self.assertTrue(any(nodes[d]['name']=='dispatch-buffer-reusable' for d in third['deps']))
        self.assertFalse(any(nodes[d]['kind']=='SEND' for d in third['deps']))
        self.assertEqual(resource_description(c)['dispatch_buffers_per_core'],2)

    def test_source_buffer_threshold_and_invalid_fields(self):
        c=fixture();c['hidden_dim']=7168;c['top_k']=16
        c['batch_limits']={'max_num_seqs':512,'max_num_batched_tokens':512}
        self.assertEqual(resource_description(c)['dispatch_buffers_per_core'],1)
        for mutate in (lambda x:x.update(send_slots_per_rank=8),
                       lambda x:x['cann_profile'].update(platform_aiv_cores=True),
                       lambda x:x['cann_profile'].update(copy_fence='submit')):
            c=fixture();mutate(c)
            with self.assertRaises(ValueError):build_plan(c)

    def test_admission_counts_and_composed_operator_boundaries(self):
        from moe_ub_cosim.route_admission import CannRouteAdmission
        from moe_ub_cosim.multilayer import build_multilayer
        c=fixture()
        for next_cost in (0,1):
            c['compute_profile_ns']['next_layer']=next_cost
            self.assertEqual(CannRouteAdmission(c).check(c['tokens'][0]),actual_usage(build_plan(c)))
        c['compute_profile_ns']['next_layer']=0
        second=deepcopy(c);second['layer_id']+=1
        for t in second['tokens']:t['layer_id']+=1
        p=build_multilayer([c,second],RunIdentities(),[[],[]])
        for g in p['rank_graphs']:
            a,b=g['layer_phases'];nodes={n['node_id']:n for n in g['nodes']}
            self.assertEqual(a['terminal_nodes'],[a['phase_nodes']['finish']])
            self.assertEqual(b['entry_deps'],a['terminal_nodes'])
            self.assertEqual(nodes[b['phase_nodes']['prepare']]['deps'],a['terminal_nodes'])
            self.assertIn('phase_members',b)

    def test_tampered_event_resource_and_cost_rejected(self):
        p=build_plan(fixture())
        for field,value in [('event','transaction_done'),('deps',[]),('resource_id','gpu_compute')]:
            q=deepcopy(p);n=next(n for n in q['rank_graphs'][0]['nodes'] if n.get('event')=='data_ready')
            n[field]=value
            with self.assertRaises(ValueError):validate_plan(q)
        q=deepcopy(p);next(n for n in q['rank_graphs'][0]['nodes'] if n['kind']=='COMP')['duration_ns']+=1
        with self.assertRaises(ValueError):validate_plan(q)

    def test_full_model_keeps_resource_and_phase_members(self):
        from moe_ub_cosim.full_model import build_full_model
        from moe_ub_cosim.full_model_service import FullModelService
        c=json.loads((ROOT/'fixtures/m10_full_model/service.json').read_text())
        segments=json.loads((ROOT/'fixtures/m10_full_model/layers.json').read_text())
        layers=segments['groups'][0]['layers']
        for layer in (l for g in segments['groups'] for l in g['layers']):
            p=layer['cann_profile'];p['platform_aiv_cores']=p.pop('aiv_lanes')
            p.update(algorithm='a3_fullmesh_v1_store_resources_tp1',core_count_basis='declared_platform_reference',copy_fence='data_ready')
            layer.update(template='cann_a3_v850_store_resources_v1',send_slots_per_rank=0)
        model=c['groups'][0]
        plan=build_full_model(layers,RunIdentities(),[[] for _ in layers],model['model_spec'],model['full_model_profile'])
        self.assertEqual(actual_usage(plan)['nodes'],sum(len(g['nodes']) for g in plan['rank_graphs']))
        for graph in plan['rank_graphs']:
            nodes={n['node_id']:n for n in graph['nodes']}
            for phase in graph['layer_phases']:
                self.assertEqual(phase['terminal_nodes'],[phase['phase_nodes']['finish']])
                for members in phase['phase_members'].values():
                    self.assertTrue(all(nodes[n]['resource_id'].startswith('aiv:') or nodes[n]['kind']=='EVENT' for n in members))
        # Keep the former KV space while accounting for the new local buffers.
        from moe_ub_cosim.model_capacity import communication_minimum
        for model,segment in zip(c['groups'],segments['groups']):
            required=sum(communication_minimum(l) for l in segment['layers'])
            for rank in model['memory']['ranks']:
                rank['device_bytes']+=required-rank['communication_buffer_bytes']
                rank['communication_buffer_bytes']=required
        FullModelService(c,segments)


if __name__=='__main__':unittest.main()
