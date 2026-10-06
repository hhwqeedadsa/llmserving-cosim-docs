"""Hand-calculated queue areas and strict public operation stage contracts."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from moe_ub_cosim.queue_statistics import resample,check_values
from moe_ub_cosim.operation_stages import validate_stages
from moe_ub_cosim.observation import validate_observation


def event(time,voq,fifo):return dict(time_ns=time,voq_waiting_bytes=voq,fifo_waiting_bytes=fifo)


class QueueStatisticsTest(unittest.TestCase):
    def test_explicit_statistic_schema_and_budget(self):
        config=json.loads((Path(__file__).resolve().parents[2]/'fixtures/m14_operation_queue/observation.json').read_text())
        validate_observation(config,[2,7,12],1000000)
        for mutate in [lambda c:c.pop('resource_budget'),lambda c:c['statistics'].update(queue_waiting_bytes=1),
                       lambda c:c['resource_budget'].update(max_windows_per_port=1),
                       lambda c:c['clocks'][-1].update(drift_ppb=-1000000000)]:
            bad=deepcopy(config);mutate(bad)
            with self.assertRaises(ValueError):validate_observation(bad,[2,7,12],1000000)

    def test_nonempty_initial_crossing_tail_and_offset(self):
        rows=list(resample([event(0,100,20),event(7,200,20),event(10,200,0),event(14,0,0)],
            dict(start_local_ns=2,end_local_ns=13,period_ns=4),dict(offset_ns=-3,drift_ppb=0),16))
        self.assertEqual([r['voq_waiting_byte_ns'] for r in rows],[600,800,200])
        self.assertEqual([r['fifo_waiting_byte_ns'] for r in rows],[80,20,0])
        self.assertEqual([r['voq_waiting_peak_bytes'] for r in rows],[200]*3)
        self.assertEqual([r['fifo_waiting_peak_bytes'] for r in rows],[20,20,0])
        self.assertEqual(rows[-1]['voq_waiting_mean_bytes'],200/3)
        for row,duration in zip(rows,[4,4,3]):check_values(row,duration)

    def test_boundary_intermediate_peak_and_huge_silent_gap(self):
        rows=list(resample([event(0,0,0),event(10,30,0),event(10,50,0),event(10,20,0),event(20,0,0)],
            dict(start_local_ns=0,end_local_ns=2**63-1,period_ns=10),dict(offset_ns=0,drift_ppb=0),20))
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['index'],1)
        self.assertEqual(rows[0]['voq_waiting_byte_ns'],200);self.assertEqual(rows[0]['voq_waiting_peak_bytes'],50)

    def test_floor_clock_and_no_left_limit_peak(self):
        rows=list(resample([event(0,9,0),event(1,7,0),event(2,0,0)],
            dict(start_local_ns=0,end_local_ns=4,period_ns=1),dict(offset_ns=0,drift_ppb=500000000),2))
        self.assertEqual([r['voq_waiting_byte_ns'] for r in rows],[9,7,7])
        self.assertEqual([r['voq_waiting_peak_bytes'] for r in rows],[9,7,7])

    def test_missing_or_incomplete_history_and_false_mean(self):
        for events in ([],[event(1,0,0)],[event(0,0,0),event(4,1,0)],[event(0,0,0),event(4,-1,0)]):
            with self.assertRaises(ValueError):list(resample(events,dict(start_local_ns=0,end_local_ns=10,period_ns=3),
                dict(offset_ns=0,drift_ppb=0),10))
        row={k+suffix:v for k in ('voq_waiting','fifo_waiting') for suffix,v in
            (('_byte_ns',2**70),('_peak_bytes',2**60),('_mean_bytes',2**60))}
        check_values(row,1024)
        row['fifo_waiting_mean_bytes']+=2**40
        with self.assertRaises(ValueError):check_values(row,1024)

    def test_stages_do_not_force_prepare_inside_old_interval(self):
        endpoint=dict(rank_id=2,local_record_id=1,kind='dispatch',start_local_ns=10,end_local_ns=30)
        rows=[dict(rank_id=2,local_record_id=1,stage=k,time_local_ns=t,event_count=n) for k,t,n in
            [('prepare_start',-2,1),('prepare_end',10,1),('request_submit_first',10,3),
             ('request_submit_last',17,3),('initiator_resources_complete',28,3),('data_ready',26,4),('operation_complete',30,1)]]
        self.assertEqual(validate_stages(rows,[endpoint]),7)
        for mutate in [lambda r:r.append(deepcopy(r[0])),lambda r:r[0].update(operation_id='secret'),
                       lambda r:r[4].update(time_local_ns=16),lambda r:r[-1].update(time_local_ns=29),
                       lambda r:r[3].update(event_count=2),lambda r:r.pop(0)]:
            bad=deepcopy(rows);mutate(bad)
            with self.assertRaises(ValueError):validate_stages(bad,[endpoint])


if __name__=='__main__':unittest.main()
