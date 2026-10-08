"""Closed observation contract and exact local-clock conversion."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.observation import backend_observation, local_time, public_config, validate_observation
from moe_ub_cosim.observation_recording import capabilities, sampling_plan
from moe_ub_cosim.research_storage import ResearchWriter, validate_observed_storage
from moe_ub_cosim.send_state import SEMANTICS, checked_rows, export_send_state, validate_policy

ROOT=Path(__file__).resolve().parents[2]
POLICY=dict(schema_version='moe-ub-send-state-config/1',max_records_per_rank=10,max_total_records=30)


class SendStateTest(unittest.TestCase):
    def setUp(self):
        self.descriptor=dict(**SEMANTICS,policy=POLICY,coverage=[dict(rank_id=2,start_local_ns=0,
            end_local_ns=100,complete=True,dropped_records=0,records=6)])
        # The full-slot wait is 2 pending sends * 10 ns. Two transitions share t=10.
        self.rows=[dict(rank_id=2,sequence=i+1,time_local_ns=t,event_kind=kind,
            ready_send_count=ready,active_send_count=active,send_slot_capacity=capacity)
            for i,(t,kind,ready,active,capacity) in enumerate([
                (0,'initial',0,0,0),(0,'update',0,0,1),(10,'update',3,1,1),
                (10,'update',2,1,1),(20,'update',0,0,1),(100,'final',0,0,1)])]

    def test_sequence_and_area(self):
        rows=list(checked_rows(self.rows,self.descriptor,[2]))
        self.assertEqual(sum(a['ready_send_count']*(b['time_local_ns']-a['time_local_ns'])
            for a,b in zip(rows,rows[1:])),20)

    def test_closed_contract(self):
        mutations=[(0,'rank_id',True),(0,'sequence',True),(0,'send_slot_capacity',1),
            (2,'time_local_ns',-1),(2,'active_send_count',2),(3,'sequence',3),
            (3,'ready_send_count',3),(5,'ready_send_count',1),(5,'time_local_ns',99),
            (5,'send_slot_capacity',2),(2,'transfer_id',10),(2,'event_kind','truth')]
        for i,key,value in mutations:
            with self.subTest(key=key,value=value):
                rows=deepcopy(self.rows);rows[i][key]=value
                with self.assertRaises(ValueError):list(checked_rows(rows,self.descriptor,[2]))
        for rows in (self.rows[:-1],self.rows[1:],self.rows+self.rows[-1:]):
            with self.assertRaises(ValueError):list(checked_rows(rows,self.descriptor,[2]))
        for key,value in [('complete',False),('dropped_records',1),('records',5)]:
            descriptor=deepcopy(self.descriptor);descriptor['coverage'][0][key]=value
            with self.assertRaises(ValueError):list(checked_rows(self.rows,descriptor,[2]))
        for value in (True,1,2**63):
            with self.assertRaises(ValueError):validate_policy({**POLICY,'max_records_per_rank':value},[2])

    def test_export_clock_and_observed_only(self):
        raw=[dict(**{k:v for k,v in row.items() if k!='time_local_ns'},time_ns=row['time_local_ns']) for row in self.rows]
        capture=dict(schema_version='moe-ub-send-state-capture/1',policy=POLICY,start_ns=0,end_ns=100,
            complete=True,dropped_records=0,records=6,ranks=[dict(rank_id=2,records=6)])
        report=dict(schema_version='moe-ub-service-result/1',send_state_capture=capture,send_state=raw)
        clock=dict(offset_ns=-23,drift_ppb=-500000000);clocks={('rank',2):clock}
        with tempfile.TemporaryDirectory() as directory:
            writer=ResearchWriter(Path(directory)/'research',1000000,10000)
            for name in ('endpoint_ops.jsonl','port_windows.csv','queue_windows.csv','endpoint_stages.jsonl','endpoint_send_state.jsonl'):
                writer.open('observed',name)
            descriptor=export_send_state(Path(directory)/'nonexistent',report,dict(send_state=POLICY),[2],100,clocks,writer)
            self.assertEqual(descriptor['coverage'][0]['start_local_ns'],-23)
            self.assertEqual(descriptor['coverage'][0]['end_local_ns'],27)
            ports=[dict(switch_id=4,port_id=0,direction='egress',start_local_ns=0,end_local_ns=100,period_ns=10)]
            public=public_config([2],ports,dict(max_ports=1,max_windows_per_port=10,max_total_windows=10),statistics=True,send_state=descriptor)
            (writer.root/'observed/public_config.json').write_text(json.dumps(public))
            writer.finish(10);writer.close()
            self.assertTrue(validate_observed_storage(writer.root/'observed')['passed'])
            # No sibling truth, execution, graph or clock-model files exist.
            with self.assertRaises(ValueError):
                export_send_state(directory,dict(schema_version='moe-ub-service-result/1'),dict(send_state=POLICY),[2],100,clocks,writer)

    def test_capture_versions_and_sampling(self):
        source=json.loads((ROOT/'examples/native_first_round/observation.json').read_text());ranks=[2,7,12]
        target=deepcopy(source);target.update(schema_version='moe-ub-observation-config/4',send_state=POLICY)
        validate_observation(target,ranks,1000000)
        backend=backend_observation(target,ranks,1000000)
        self.assertEqual(backend['schema_version'],'moe-ub-port-observation-set/5')
        self.assertTrue(all(p['schema_version']=='moe-ub-port-observation/4' for p in backend['ports']))
        self.assertEqual(capabilities(source,ranks,1000000)['schema_version'],'moe-ub-capture-capabilities/1')
        self.assertEqual(capabilities(target,ranks,1000000)['schema_version'],'moe-ub-capture-capabilities/2')
        with self.assertRaises(ValueError):sampling_plan(source,target,ranks,1000000)
        sampled=deepcopy(target)
        for c in sampled['clocks']:
            if c['device_type']=='rank':c.update(offset_ns=-17,drift_ppb=-12345)
        sampling_plan(target,sampled,ranks,1000000)
        bad=deepcopy(target);bad.pop('recording')
        with self.assertRaises(ValueError):validate_observation(bad,ranks,1000000)


if __name__=='__main__':unittest.main()
