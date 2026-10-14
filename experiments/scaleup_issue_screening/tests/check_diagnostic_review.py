"""Independent arithmetic checks for stage progress and historical bounds."""
import unittest
from ..analysis.diagnostic_deep import progress,distribution
from ..analysis.diagnostic_historical import bounds
from ..analysis.diagnostic_compare import payload_cost,business_delta,filtered_cost


class ReviewMathTest(unittest.TestCase):
    def test_progress_is_weighted_work_not_packet_count(self):
        packets=[dict(t=30,business_bytes=80),dict(t=10,business_bytes=10),
                 dict(t=20,business_bytes=10),dict(t=100,business_bytes=0)]
        self.assertEqual(progress(packets,'t'),dict(bytes=100,t50_ns=30,t90_ns=30,t99_ns=30))
        self.assertIsNone(progress([], 't')['t90_ns'])

    def test_no_queue_peak_coincidence_is_assumed(self):
        self.assertEqual(bounds(1001,20,30),dict(peak_lower=30,peak_upper=50,nonempty_time_lower_ns=21))
        self.assertEqual(bounds(0,0,0)['nonempty_time_lower_ns'],0)
        with self.assertRaises(ValueError):bounds(1,0,0)

    def test_empty_and_discrete_wait_distribution(self):
        self.assertEqual(distribution([]),dict(n=0))
        r=distribution([0,1,2,97]);self.assertEqual((r['sum'],r['median'],r['p95']),(100,2,97))

    def test_histogram_end_burst_and_common_cost_are_not_omitted(self):
        c=dict(common_endpoint_payload_bytes=10,common_stage_payload_bytes=20,common_workload_payload_bytes=30,
            report_bytes=100,histogram_bytes=200,peak_report_bytes_per_1us=40,counter_bank_bytes=8,
            histogram_state_bytes=132,ring_bytes=88,completion_timing_state_bound_bytes=12)
        r=payload_cost(c,1000)
        self.assertEqual((r['combined_payload_bytes'],r['peak_network_bytes_per_1us'],
            r['measured_counter_state_lower_bound_bytes']),(360,240,240))

    def test_business_delta_keeps_new_latest_consumer(self):
        a=dict(simulation_end_ns=110,last_model_compute_ns=105,
            operations=[dict(operation_id='x',end_ns=100,last_ranks=[1,2])])
        b=dict(simulation_end_ns=122,last_model_compute_ns=117,
            operations=[dict(operation_id='x',end_ns=112,last_ranks=[3])])
        d=business_delta(b,a)
        self.assertEqual(d['final_operator_delta_ns'],12)
        self.assertEqual(d['operations'][0]['baseline_last_ranks'],[1,2])
        self.assertEqual(d['operations'][0]['last_ranks'],[3])

    def test_local_filter_preserves_predictions_and_charges_coverage_burst(self):
        rows=[dict(end_ns=1000),dict(end_ns=1000,reported_at_ns=2000)]
        r=filtered_cost(rows,2,2000,88)
        self.assertEqual((r['candidate_records'],r['network_payload_bytes'],r['peak_network_bytes_per_1us']),(2,216,128))
        self.assertEqual(filtered_cost([],2,2000,88)['network_payload_bytes'],40)


if __name__=='__main__':unittest.main()
