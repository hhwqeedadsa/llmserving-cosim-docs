"""Event denominators and report-width penalties independent of classifiers."""
import unittest
from ..analysis.diagnostic_evaluate import score,union

class EvaluationTest(unittest.TestCase):
    def test_event_is_counted_once_and_broad_candidate_time_is_penalized(self):
        e=dict(node_id=1,port_id=2,start_ns=10,end_ns=20,slow_service_overlap=False)
        p=dict(node_id=1,port_id=2,start_ns=0,end_ns=100,labels=['queue_cause_unknown'],possible_ranks=[0,1])
        r=score([e],[p,p],[],100)
        self.assertEqual(r['detected_events'],1)
        self.assertEqual(r['candidate_interval_groups'],1)
        self.assertEqual(r['false_candidate_port_ns'],90)
        self.assertIsNone(r['credit_recall'])

    def test_credit_checks_do_not_fill_gaps(self):
        c=dict(node_id=1,port_id=2,start_ns=10,failed_checks_ns=[10,30])
        p=dict(node_id=1,port_id=2,start_ns=15,end_ns=20,labels=['credit_gate_witness_root_unknown'],possible_ranks=[0])
        self.assertEqual(score([], [p],[c],100)['credit_witness_detected'],0)
        p.update(start_ns=29,end_ns=31)
        self.assertEqual(score([], [p],[c],100)['credit_witness_detected'],1)
        self.assertEqual(union([(0,10),(5,20),(21,30)]),[(0,20),(21,30)])

if __name__=='__main__':unittest.main()
