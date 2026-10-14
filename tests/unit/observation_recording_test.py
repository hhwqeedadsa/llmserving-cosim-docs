"""Capture capability refusal before expensive execution/export work."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.observation import backend_observation
from moe_ub_cosim.observation_recording import CATEGORIES,capabilities,sampling_plan,first_global
from moe_ub_cosim.queue_statistics import resample


class ObservationRecordingTest(unittest.TestCase):
    def config(self):
        root=Path(__file__).resolve().parents[2]
        c=json.loads((root/'fixtures/m14_operation_queue/observation.json').read_text())
        c['schema_version']='moe-ub-observation-config/3'
        c['recording']=dict(schema_version='moe-ub-observation-recording/1',mode='aggregate',detail_ranges=[],
            truth_categories=list(CATEGORIES),max_detail_events_per_port=10000,max_window_attribution_entries=100)
        return c

    def test_aligned_merging_and_rejecting_missing_information(self):
        c=self.config();target=deepcopy(c);target['switches'][0]['period_ns']=1270
        self.assertEqual(sampling_plan(c,target,[2,7,12],1000000),{(4,4):'aggregate'})
        for mutate in [lambda t:t['switches'][0].update(period_ns=1),lambda t:t['switches'][0].update(start_local_ns=1),
                       lambda t:t['switches'][0].update(end_local_ns=999999),lambda t:t['clocks'][-1].update(drift_ppb=1),
                       lambda t:t['switches'][0].update(port_ids=[5])]:
            bad=deepcopy(target);mutate(bad)
            with self.assertRaises(ValueError):sampling_plan(c,bad,[2,7,12],1000000)
        partial=deepcopy(target);partial['switches'][0].update(start_local_ns=127,end_local_ns=381,period_ns=254)
        self.assertEqual(sampling_plan(c,partial,[2,7,12],1000000),{(4,4):'aggregate'})

    def test_selected_detail_with_clock_floor_boundary(self):
        c=self.config();c['recording']['detail_ranges']=[dict(switch_id=4,port_id=4,start_ns=10,end_ns=100)]
        t=deepcopy(c);t['clocks'][-1].update(drift_ppb=-500000000)
        t['switches'][0].update(start_local_ns=5,end_local_ns=50,period_ns=3)
        self.assertEqual(sampling_plan(c,t,[2,7,12],1000000),{(4,4):'detailed'})
        # global 9 and 10 can map to one tick under another fractional clock;
        # an initial snapshot at 10 cannot recover transient peaks before it.
        t['clocks'][-1].update(drift_ppb=-600000000);t['switches'][0].update(start_local_ns=3,end_local_ns=40)
        self.assertEqual(first_global(3,t['clocks'][-1]),8)
        with self.assertRaises(ValueError):sampling_plan(c,t,[2,7,12],1000000)
        caps=capabilities(c,[2,7,12],1000000)
        self.assertEqual(caps['ports'][0]['details'],dict(start_ns=10,end_ns=100))
        self.assertTrue(caps['queue_statistics']);self.assertEqual(caps['truth_categories'],list(CATEGORIES))
        b=backend_observation(c,[2,7,12],1000000)
        self.assertEqual(b['schema_version'],'moe-ub-port-observation-set/4')
        self.assertEqual(b['ports'][0]['recording']['detail_range'],dict(start_ns=10,end_ns=100))

    def test_old_detailed_capabilities_and_unrecorded_statistics(self):
        c=self.config();c.pop('recording');c.pop('statistics');c.pop('schema_version')
        caps=capabilities(c,[2,7,12],1000000)
        self.assertEqual(caps['storage'],'detailed');self.assertFalse(caps['queue_statistics'])
        self.assertEqual(caps['truth_categories'],['business_data'])
        with self.assertRaises(ValueError):sampling_plan(c,self.config(),[2,7,12],1000000)
        t=deepcopy(c);t['switches'][0].update(start_local_ns=17,end_local_ns=33,period_ns=1)
        self.assertEqual(sampling_plan(c,t,[2,7,12],1000000),{(4,4):'detailed'})

    def test_network_facts_are_private_and_cannot_be_added_by_sampling(self):
        c=self.config();t=deepcopy(c)
        t.update(schema_version='moe-ub-observation-config/6',network_facts=dict(
            schema_version='moe-ub-network-facts/1',ports=[dict(node_id=4,port_id=4)],
            end_ns=1000000,max_records=10000))
        with self.assertRaises(ValueError):sampling_plan(c,t,[2,7,12],1000000)
        self.assertEqual(sampling_plan(t,t,[2,7,12],1000000),{(4,4):'aggregate'})
        self.assertEqual(backend_observation(t,[2,7,12],1000000)['schema_version'],'moe-ub-port-observation-set/7')
        self.assertEqual(capabilities(t,[2,7,12],1000000)['network_facts_truth']['access_scope'],'internal_truth_not_observed')

    def test_partial_state_initial_is_not_a_left_limit_peak(self):
        events=[dict(event_kind=k,time_ns=t,voq_waiting_bytes=v,fifo_waiting_bytes=f) for k,t,v,f in
                [('initial',10,11,0),('update',10,0,0),('update',15,0,3),('final',20,0,3)]]
        port=dict(start_local_ns=10,end_local_ns=20,period_ns=10);clock=dict(offset_ns=0,drift_ppb=0)
        rows=list(resample(events,port,clock,100,capture=dict(start_ns=10,end_ns=20)))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['voq_waiting_peak_bytes'],0)
        self.assertEqual(rows[0]['fifo_waiting_byte_ns'],15);self.assertEqual(rows[0]['fifo_waiting_peak_bytes'],3)
        with self.assertRaises(ValueError):list(resample(events[:-1],port,clock,100,capture=dict(start_ns=10,end_ns=20)))


if __name__=='__main__':unittest.main()
