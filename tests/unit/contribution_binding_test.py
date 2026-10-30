"""Artificial callbacks check binding logic; no network or GPU is executed."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.contribution_attempts import ContributionAttempts
from moe_ub_cosim.contribution_binding import ContributionBinding, configuration, preflight
from moe_ub_cosim.execution_events import ExecutionObserver


def config():
    return dict(schema_version='moe-scaleout-service/1', model=dict(experts=4,layers=1), compute={},workload=dict(decode_steps=1),
        parallel=dict(groups=[[0,1]]), route_replay={}, transport={},
        phase_completion=dict(mode='ep_transport_flush/1'),
        token_transport=dict(mode='deepep_direct_bf16/1',combine='rank_reduced/1'),
        execution_events=dict(schema_version='moe-execution-events/1',max_events=128),
        contribution_tracking=dict(schema_version='moe-contribution-binding/1',namespace='unit-session',
            max_invocations=32,max_bundles=64,max_lifecycle_events=1024,max_report_bytes=2**20))


class Routes:
    records={i:dict(original_path=f'unit/{i}.json') for i in range(2)}

    def owner(self,group,layer,expert):return group[expert//2]

    def routes(self,group,step,layer):
        return [dict(rank=0,slot=i,experts=[0,1,2],source=dict(request_id=i,file_id=f'unit-{i}',
                    source_entry=step+1,source_token_row=0,source_layer_id=3,sha256=str(i)*64)) for i in range(2)]


def wave():
    dispatch=dict(id=7,src=0,dst=1,bytes=1024,layer=0,phase='dispatch')
    combine=dict(id=8,src=1,dst=0,bytes=1024,layer=0,phase='combine')
    def node(i,name,kind,deps,duration=0,message=None):
        row=dict(id=i,name='L0.'+name,kind=kind,deps=deps,duration_ns=duration)
        if message:row['message']=message
        return row
    return dict(graph_id='g0.w0',group_index=0,decode_step=0,rank_ids=[0,1],
        routes=[dict(layer=0,tokens=Routes().routes([0,1],0,0))],messages=[dispatch,combine],rank_nodes={
            '0':[node(1,'pack','COMP',[],2),node(2,'dispatch.send','SEND',[1],message=dispatch),
                 node(3,'experts','COMP',[2],2),node(4,'combine_reduce','COMP',[3],3),
                 node(5,'combine.recv','RECV',[],message=combine)],
            '1':[node(1,'pack','COMP',[]),node(2,'dispatch.recv','RECV',[],message=dispatch),
                 node(3,'experts','COMP',[1,2],2),node(4,'combine_reduce','COMP',[3],3),
                 node(5,'combine.send','SEND',[4],message=combine)]})


def events():
    rows=[]
    def n(kind,t,rank,node):rows.append(dict(kind='node_'+kind,time_ns=t,graph_id='g0.w0',rank=rank,node=node))
    def m(kind,t,rank,message):rows.append(dict(kind='message_'+kind,time_ns=t,graph_id='g0.w0',rank=rank,message=message))
    n('issue',0,0,1);n('issue',0,1,1);n('complete',0,1,1)
    n('issue',0,1,2);m('recv_post',0,1,7)
    n('issue',0,0,5);m('recv_post',0,0,8)
    n('complete',2,0,1);n('issue',2,0,2);m('send_post',2,0,7)
    m('recv_complete',4,1,7);n('complete',4,1,2)
    m('send_complete',6,0,7);n('complete',6,0,2)
    n('issue',6,0,3);n('issue',6,1,3)
    n('complete',8,0,3);n('complete',8,1,3)
    n('issue',8,0,4);n('issue',8,1,4)
    n('complete',11,0,4);n('complete',11,1,4)
    n('issue',11,1,5);m('send_post',11,1,8)
    m('recv_complete',13,0,8);n('complete',13,0,5)
    m('send_complete',15,1,8);n('complete',15,1,5)
    rows += [dict(kind='graph_retired',time_ns=15,graph_id='g0.w0'),dict(kind='service_drained',time_ns=20)]
    return [dict(row,type='OBSERVE',schema_version='moe-execution-event/1',sequence=i) for i,row in enumerate(rows)]


class BindingChecks(unittest.TestCase):
    def runtime(self,c=None):
        c=c or config();runtime=ContributionBinding(c,Routes(),ExecutionObserver(c['execution_events']))
        runtime.register(wave());return runtime

    def test_live_input_compute_result_and_cleanup_are_separate(self):
        runtime=self.runtime()
        for event in events()[:12]:runtime.accept(event)
        for row in runtime.records():
            state=row['state'];self.assertEqual(state['accepted_experts'],[])
            self.assertEqual(state['attempts']['original.r0']['input_ready_ns'],2)
            self.assertEqual(state['attempts']['original.r1']['input_ready_ns'],4)
        for event in events()[12:26]:runtime.accept(event)
        for row in runtime.records():
            state=row['state'];self.assertEqual(state['logical_complete_ns'],13)
            self.assertEqual(state['accepted_experts'],[0,1,2]);self.assertFalse(state['declared_call_retirable'])
            self.assertIsNone(state['resources']['combine.8.r1']['closed_ns'])
            self.assertEqual(state['attempts']['original.r1']['compute_done_ns'],8)
        for event in events()[26:]:runtime.accept(event)
        self.assertEqual(runtime.summary()['retired_calls'],2)
        self.assertEqual(runtime.summary()['lifecycle_events'],40)
        self.assertEqual(runtime.summary()['reserved_lifecycle_events'],40)

    def test_two_slots_share_one_message_without_sharing_call_identity(self):
        runtime=self.runtime();rows=list(runtime.records())
        self.assertNotEqual(rows[0]['state']['keys'],rows[1]['state']['keys'])
        self.assertEqual(rows[0]['binding']['bundles'][1]['dispatch_message'],7)
        self.assertEqual(rows[1]['binding']['bundles'][1]['dispatch_message'],7)
        self.assertEqual(len(runtime.observer.messages),2)
        self.assertEqual(rows[0]['spec']['bundles'][0]['experts'],[0,1])

    def test_no_activation_hash_and_source_coordinate_is_explicit(self):
        row=next(self.runtime().records());inv=row['spec']['invocation']
        self.assertNotIn('input_sha256',inv)
        self.assertEqual(inv['input_identity']['kind'],'simulation_lineage/1')
        self.assertEqual(inv['token_coordinate'],dict(kind='route_decode_entry/1',source_entry=1,source_token_row=0))
        self.assertEqual(inv['source_layer'],3)

    def test_lineage_generations_request_and_coordinate_change_key(self):
        row=next(self.runtime().records());spec=row['spec'];key=ContributionAttempts(spec).bundle_key('r0')
        for change in ('request','entry','producer','namespace','layer'):
            s=deepcopy(spec)
            if change=='request':s['invocation']['request_id']='other'
            elif change=='entry':s['invocation']['token_coordinate']['source_entry']+=1
            elif change=='producer':s['invocation']['input_identity']['producer_node']+=1
            elif change=='namespace':s['invocation']['input_identity']['namespace']='other'
            else:s['invocation']['source_layer']+=1
            self.assertNotEqual(key,ContributionAttempts(s).bundle_key('r0'))

    def test_missing_route_identity_or_foreign_choices_rejected_before_registration(self):
        for change in ('source','experts','reuse'):
            runtime=ContributionBinding(config(),Routes(),ExecutionObserver(config()['execution_events']))
            w=wave()
            if change=='source':w['routes'][0]['tokens'][0]['source']['request_id']=5
            elif change=='experts':w['routes'][0]['tokens'][0]['experts']=[0,1,3]
            else:runtime.register(w)
            before=runtime.summary()
            with self.assertRaises(ValueError):runtime.register(w)
            self.assertEqual(runtime.summary(),before)

    def test_budget_refuses_before_any_observer_or_call_is_registered(self):
        for name,value in [('max_invocations',1),('max_bundles',3),('max_lifecycle_events',39)]:
            c=config();c['contribution_tracking'][name]=value
            runtime=ContributionBinding(c,Routes(),ExecutionObserver(c['execution_events']))
            with self.assertRaisesRegex(ValueError,'budget'):runtime.register(wave())
            self.assertFalse(runtime.calls);self.assertFalse(runtime.observer.graphs)

    def test_configuration_cannot_enable_without_prerequisites(self):
        self.assertIsNone(configuration({}))
        for field in ('route_replay','execution_events','transport','phase_completion','token_transport'):
            c=config();del c[field]
            with self.assertRaises(ValueError):configuration(c)
        c=config();c['contribution_tracking']['max_invocations']=True
        with self.assertRaises(ValueError):configuration(c)

    def test_wrong_or_duplicate_native_event_does_not_advance_core(self):
        runtime=self.runtime();event=events()[0];runtime.accept(event)
        before=[r['state'] for r in runtime.records()]
        with self.assertRaises(ValueError):runtime.accept(event)
        self.assertEqual(before,[r['state'] for r in runtime.records()])

    def test_cumulative_preflight_counts_actual_rank_bundles(self):
        c=config()
        self.assertEqual(preflight(c,Routes()),dict(invocations=2,bundles=4,lifecycle_events=40))
        for name,value in [('max_invocations',1),('max_bundles',3),('max_lifecycle_events',39)]:
            changed=deepcopy(c);changed['contribution_tracking'][name]=value
            with self.assertRaisesRegex(ValueError,'cumulative budget'):preflight(changed,Routes())

    def test_new_identity_schema_rejects_untyped_or_mixed_fingerprints(self):
        spec=next(self.runtime().records())['spec']
        for change in ('tensor-hash','unknown-kind','coordinate','bool-producer'):
            s=deepcopy(spec);inv=s['invocation']
            if change=='tensor-hash':inv['input_sha256']='a'*64
            elif change=='unknown-kind':inv['input_identity']['kind']='actual_tensor'
            elif change=='coordinate':inv['token_coordinate']['source_entry']=0
            else:inv['input_identity']['producer_rank']=True
            with self.assertRaises(ValueError):ContributionAttempts(s)

    def test_bounded_outputs_and_valid_incomplete_prefix(self):
        runtime=self.runtime();runtime.accept(events()[0])
        with tempfile.TemporaryDirectory() as folder:
            runtime.write(Path(folder))
            summary=json.loads((Path(folder)/'contribution_summary.json').read_bytes())
            self.assertEqual(summary['logical_complete_calls'],0)
            self.assertIsNone(summary['service_drained_ns'])
        runtime.option=deepcopy(runtime.option);runtime.option['max_report_bytes']=1
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'byte budget'):runtime.write(Path(folder))


if __name__=='__main__':unittest.main()
