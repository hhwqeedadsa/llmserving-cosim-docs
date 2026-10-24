"""Admission/causal controller checks. Synthetic callbacks are NOT a network."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.modern_native import ModernNativeController
from moe_ub_cosim.modern_native_layout import ModernNativeLayout
from moe_ub_cosim.scaleout_transactions import validate

ROOT=Path(__file__).resolve().parents[2]


def example():return json.loads((ROOT/'examples/modern_deepep_native/input.json').read_text())
def protocol():return json.loads((ROOT/'examples/modern_deepep_native/rdma.json').read_text())
def topology():
    return dict(schema_version='moe-scaleout-physical-topology/1',nodes=[dict(id=i,kind='gpu') for i in range(8)]+[dict(id=8,kind='tor',plane=0),dict(id=9,kind='tor',plane=1)],
        links=[dict(src=i,dst=8+p,src_port=1+p,dst_port=i+1,rate_bps=100000000000,delay_ns=1000) for i in range(8) for p in range(2)])


def declared_callback_drive(config):
    controller=ModernNativeController(topology(),protocol(),config);sent=set();rows=[];now=0
    for _ in range(200):
        nodes=controller.advance(dict(now_ns=now,events=rows,drained=False));rows=[]
        program=deepcopy(controller.layout.initial);program['operations']=controller.native_nodes
        validate(topology(),protocol(),program,allow_no_remote=True)
        for node in controller.native_nodes:
            if node['id'] in sent:continue
            if not all((d['operation'],d['event']) in controller.callbacks for d in node['after']):continue
            if node['kind']=='local':events=['local_admitted','local_started','completed']
            else:
                events=['submitted','prepared','published','applied']
                if 'self_service' not in controller.layout.connections[node['connection']]:
                    events+=['request_acked']
                    if node['verb']=='fetch_add':events+=['response']
                events+=['completed']
            rows.extend(dict(operation=node['id'],event=e,time_ns=now+1) for e in events);sent.add(node['id'])
        if controller.runtime.snapshot()['all_events_fired']:return controller
        if not rows:raise AssertionError('declared transcript cannot enable a next step')
        now+=1
    raise AssertionError('synthetic transcript did not close')


class ModernNativeTests(unittest.TestCase):
    def test_explicit_windows_counts_signal_and_persistent_identity(self):
        layout=ModernNativeLayout(topology(),protocol(),example())
        self.assertEqual(len(layout.initial['windows']),6)
        self.assertTrue(any('self_service' in c for c in layout.connections))
        self.assertTrue(any(c.get('plane')==1 for c in layout.connections))
        self.assertEqual(len(layout.by_identity),len(layout.connections))
        rank0=bytes.fromhex(layout.segments[0][0]['hex'])
        self.assertEqual([int.from_bytes(rank0[i:i+8],'little',signed=True) for i in range(0,len(rank0),8)],[-3,-3,-2,-2,-2,-2])

    def test_ready_never_manufactures_native_completion(self):
        c=ModernNativeController(topology(),protocol(),example())
        nodes=c.advance(dict(now_ns=0,events=[],drained=True))
        self.assertEqual(len(nodes),4)
        self.assertFalse(c.runtime.snapshot()['all_application_complete'])
        self.assertFalse(any(e['kind'] in ('accepted','visible','local_complete') for e in c.log))

    def test_full_declared_transcript_retains_all_modern_operations_and_epochs(self):
        c=declared_callback_drive(example());state=c.snapshot();runtime=state['runtime']
        self.assertTrue(runtime['all_events_fired']);self.assertFalse(runtime['memory_state']['active_source_leases'])
        self.assertEqual(runtime['memory_state']['nonzero_count_elements'],0)
        self.assertEqual(len(runtime['memory_state']['completed_barriers']),16)
        self.assertTrue(all(s['value']==s['shadow']==8 for s in runtime['memory_state']['signals']))
        actual=Counter(t['path'] for t in c.tasks.values() if t['role']=='operation')
        expected=Counter(o['path'] for o in c.layout.plan['operations']);self.assertEqual(actual,expected)
        self.assertTrue(any(n.get('aggregate_requests') for n in c.native_nodes))
        self.assertTrue(any(o.get('receive_index',0)>0 for o in runtime['log']))

    def test_out_of_order_or_unknown_or_duplicate_callback_rejected(self):
        for event in ('completed','applied','response'):
            c=ModernNativeController(topology(),protocol(),example());c.advance(dict(now_ns=0,events=[],drained=True))
            with self.subTest(event=event),self.assertRaisesRegex(ValueError,'prerequisite'):
                c.advance(dict(now_ns=1,events=[dict(operation=0,event=event,time_ns=1)],drained=False))
        c=ModernNativeController(topology(),protocol(),example());c.advance(dict(now_ns=0,events=[],drained=True))
        with self.assertRaisesRegex(ValueError,'unknown or duplicate'):
            c.advance(dict(now_ns=1,events=[dict(operation=65535,event='submitted',time_ns=1)],drained=False))

    def test_gpu_visibility_implicit_costs_and_bad_self_service_rejected(self):
        for change in (lambda c:c['provider'].update(target_visibility='gpu_visible'),
                       lambda c:c['costs'].pop('expert_ns'),lambda c:c['costs'].update(expert_ns=True),
                       lambda c:c['provider']['self_service'].update(complete_ns=0),
                       lambda c:c['provider'].update(queue_depth=3)):
            cfg=example();change(cfg)
            with self.assertRaises(ValueError):ModernNativeLayout(topology(),protocol(),cfg)

    def test_nic_plane_device_and_rank_mapping_rejected(self):
        for change in (lambda c:c['deployment'][0].update(node=7),
                       lambda c:c['deployment'][0]['devices'][0].update(nic=3),
                       lambda c:c['deployment'][0]['devices'][0].update(plane=1),
                       lambda c:c['deployment'][0]['devices'][0].update(device=2),
                       lambda c:c['deployment'][1].update(node=0)):
            cfg=example();change(cfg)
            if cfg['deployment'][0]['node']==7:
                # Seven is a legitimate node; the mapping changes physically.
                self.assertEqual(ModernNativeLayout(topology(),protocol(),cfg).node(0),7)
            else:
                with self.assertRaises(ValueError):ModernNativeLayout(topology(),protocol(),cfg)

    def test_input_snapshot_isolated_from_caller_mutation(self):
        cfg=example();c=ModernNativeController(topology(),protocol(),cfg);cfg['costs']['expert_ns']=999999
        self.assertEqual(c.layout.config['costs']['expert_ns'],1000)

    def test_declared_serialization_budgets_are_admission_limits(self):
        for key in ('max_input_bytes','max_output_bytes'):
            cfg=example();cfg['modern']['limits'][key]=1
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'serialized'):
                ModernNativeLayout(topology(),protocol(),cfg)

    def test_duplicate_native_callback_is_not_a_second_transition(self):
        c=ModernNativeController(topology(),protocol(),example());c.advance(dict(now_ns=0,events=[],drained=True))
        callback=dict(operation=0,event='submitted',time_ns=1)
        c.advance(dict(now_ns=1,events=[callback],drained=False))
        with self.assertRaisesRegex(ValueError,'unknown or duplicate'):
            c.advance(dict(now_ns=1,events=[callback],drained=False))


if __name__=='__main__':unittest.main()
