"""Artificial research inputs: no simulator fixtures, hidden flow or old runs."""
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.mapping_value.analyze import analyze, endpoint_differences, select_call
from experiments.mapping_value.common import digest, read, save, write_rows
from experiments.mapping_value.inputs import tables, validate
from experiments.mapping_value.prepare import anchors, project_relations


def hand_input(root, condition='E3'):
    root.mkdir()
    public = dict(schema_version='mapping-value-public/1',group_id='ep0',ranks=[0,1],anchor_rank=0,
        rank_nodes={'0':0,'1':1},nodes=[dict(node_id=0,kind='DEVICE',ports=1),dict(node_id=1,kind='DEVICE',ports=1),dict(node_id=2,kind='SWITCH',ports=2)],
        links=[dict(node_a=i,port_a=0,node_b=2,port_b=i,rate_bps=1000000000,delay_ns=20) for i in (0,1)],
        ports=[dict(switch_id=2,port_id=i,direction='egress',start_local_ns=0,end_local_ns=1000,period_ns=100) for i in (0,1)],
        queue_ports=[[2,0]],clock_relation='shared_accurate_ns',time_tolerance_ns=1,
        material_round_absolute_ns=10,material_round_relative=0.001,analysis_version='candidate-evidence-v3')
    save(root/'public.json',public)
    for prefix in ('','reference_'):
        end=20 if prefix else 35
        ep=[dict(rank_id=0,local_record_id=1,kind='dispatch',start_local_ns=10,end_local_ns=end),
            dict(rank_id=1,local_record_id=1,kind='dispatch',start_local_ns=200,end_local_ns=280)]
        port=dict(switch_id=2,port_id=0,direction='egress',local_window_start_ns=200,local_window_end_ns=300)
        data=dict(endpoint=ep,stages=[dict(rank_id=r['rank_id'],local_record_id=1,stage='operation_complete',time_local_ns=r['end_local_ns'],event_count=1) for r in ep],
            ports=[dict(port,counted_packets=1,counted_packet_bytes=100 if prefix else 200)],
            queues=[dict(port,voq_waiting_mean_bytes=0,fifo_waiting_mean_bytes=10 if prefix else 50,
                         voq_waiting_peak_bytes=0,fifo_waiting_peak_bytes=100)],
            relations=[dict(port,rank_id=0,local_record_id=1)])
        for name in tables(condition):
            write_rows(root/(prefix+name+'.jsonl'),data[name])
    seal(root,condition)
    return root


def seal(root, condition=None):
    if condition is None:
        condition=read(root/'condition.json')['condition']
    save(root/'condition.json',dict(schema_version='mapping-value-input/1',condition=condition,
        files={p.name:digest(p) for p in root.iterdir() if p.name!='condition.json'}))


class InputBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_information_condition_and_outside_anchor_interval(self):
        for condition in ('E1','E2','E3','E4','E5','E3S','E5S'):
            source=hand_input(self.root/condition,condition)
            validate(source)
            analyze(source,self.root/(condition+'.json'))
            prediction=read(self.root/(condition+'.json'))
            self.assertEqual(prediction['selected_call'],dict(rank_id=0,local_record_id=1,kind='dispatch'))
            if condition in ('E3','E5','E3S','E5S'):
                self.assertEqual(len(prediction['port_candidates']),1)
                self.assertEqual(prediction['association_rule'],'ideal_binary_windows')

    def test_no_side_channel_files_even_when_manifest_resealed(self):
        source=hand_input(self.root/'input')
        save(source/'truth.json',dict(operation_id='hidden'))
        seal(source)
        with self.assertRaisesRegex(ValueError,'whitelist'):
            validate(source)

    def test_endpoint_hidden_field_rejected(self):
        source=hand_input(self.root/'input')
        path=source/'endpoint.jsonl'
        data=[json.loads(line) for line in path.read_text().splitlines()]
        data[0]['operation_id']='hidden'; write_rows(path,data); seal(source)
        with self.assertRaisesRegex(ValueError,'endpoint fields'):
            validate(source)

    def test_relation_weights_duplicate_unknown_and_out_of_scope(self):
        for index,change in enumerate(('weight','duplicate','unknown','scope')):
            source=hand_input(self.root/str(index)); path=source/'relations.jsonl'
            data=[json.loads(line) for line in path.read_text().splitlines()]
            if change=='weight':data[0]['business_payload_bytes']=100
            elif change=='duplicate':data.append(copy.deepcopy(data[0]))
            elif change=='unknown':data[0]['local_record_id']=99
            else:data[0]['local_window_start_ns']=1000;data[0]['local_window_end_ns']=1100
            write_rows(path,data);seal(source)
            with self.assertRaises(ValueError):validate(source)

    def test_no_queue_or_mapping_in_e2(self):
        source=hand_input(self.root/'input','E2')
        write_rows(source/'queues.jsonl',[]);seal(source)
        with self.assertRaisesRegex(ValueError,'whitelist'):validate(source)

    def test_symlink_rejected(self):
        source=hand_input(self.root/'input');path=source/'endpoint.jsonl'
        moved=self.root/'outside';path.rename(moved);path.symlink_to(moved)
        with self.assertRaisesRegex(ValueError,'symlink'):validate(source)

    def test_stage_incomplete_rejected(self):
        source=hand_input(self.root/'input')
        path=source/'stages.jsonl';data=[json.loads(line) for line in path.read_text().splitlines()]
        data.append(dict(rank_id=0,local_record_id=1,stage='prepare_end',time_local_ns=9,event_count=1))
        write_rows(path,data);seal(source)
        with self.assertRaisesRegex(ValueError,'Incomplete'):validate(source)

    def test_local_duration_not_propagated_shift(self):
        normal=[dict(rank_id=0,local_record_id=1,kind='dispatch',start_local_ns=10,end_local_ns=20)]
        current=[dict(normal[0],start_local_ns=60,end_local_ns=70)]
        result=endpoint_differences(current,normal)[0]
        self.assertEqual(result['duration_delta_ns'],0)
        self.assertEqual(result['completion_shift_ns'],50)

    def test_window_mapping_and_explicit_state_extension(self):
        counts={}
        for condition in ('E2','E3','E3S'):
            source=hand_input(self.root/condition,condition)
            for prefix in ('','reference_'):
                path=source/(prefix+'ports.jsonl')
                data=[json.loads(line) for line in path.read_text().splitlines()]
                data.append(dict(data[0],port_id=1));write_rows(path,data)
                if condition=='E3S':
                    path=source/(prefix+'relations.jsonl')
                    data=[json.loads(line) for line in path.read_text().splitlines()]
                    data.append(dict(data[0],port_id=1));write_rows(path,data)
            seal(source)
            target=self.root/(condition+'.json');analyze(source,target)
            counts[condition]=len(read(target)['port_candidates'])
        self.assertEqual(counts,{'E2':2,'E3':1,'E3S':2})

    def test_unchanged_anchor_does_not_hide_other_rank_delay(self):
        changes=[dict(rank_id=r,local_record_id=1,kind='dispatch',duration_delta_ns=0 if r==0 else 50,
                      completion_shift_ns=0 if r==0 else 50) for r in (0,1)]
        self.assertEqual(select_call(changes,0,10)['rank_id'],0)
        with self.assertRaisesRegex(ValueError,'matching per-rank'):
            select_call(changes+[dict(changes[1],local_record_id=2)],0,10)

    def test_analysis_import_boundary(self):
        directory=Path(__file__).resolve().parents[1]
        for name in ('analyze.py','inputs.py','common.py'):
            tree=ast.parse((directory/name).read_text())
            for node in ast.walk(tree):
                if isinstance(node,ast.ImportFrom):
                    self.assertNotIn(node.module,('prepare','evaluate','workflow','cases'))
                    self.assertFalse((node.module or '').startswith('moe_ub_cosim'))


class ProjectionTests(unittest.TestCase):
    def test_unique_anchor_and_no_rank_expansion(self):
        endpoints=[dict(rank_id=r,local_record_id=1,kind='dispatch') for r in (0,1)]
        operation=dict(operation_id='private',group_id='ep0',kind='dispatch',endpoint_records=endpoints)
        mapped=anchors([operation],endpoints,'ep0',0)
        source=dict(operation_id='private',switch_id=2,port_id=0,direction='egress',
            local_window_start_ns=200,local_window_end_ns=300,business_payload_bytes=64)
        result=list(project_relations([source,dict(source,business_payload_bytes=32)],mapped))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['rank_id'],0)
        self.assertNotIn('operation_id',result[0]);self.assertNotIn('business_payload_bytes',result[0])
        for records in ([endpoints[1]],[endpoints[0],endpoints[0]]):
            with self.assertRaisesRegex(ValueError,'exactly one'):
                anchors([dict(operation,endpoint_records=records)],endpoints,'ep0',0)

    def test_state_category_is_explicit(self):
        base=dict(operation_id='private',switch_id=2,port_id=0,direction='egress',
                  local_window_start_ns=0,local_window_end_ns=100,application_bytes=32)
        source=[dict(base,message_category=c) for c in ('business_data','dispatch_count','attached_metadata','protocol_ack')]
        result=list(project_relations(source,{'private':(0,1)},categories={'dispatch_count'}))
        self.assertEqual(len(result),1)


if __name__=='__main__':
    unittest.main()
