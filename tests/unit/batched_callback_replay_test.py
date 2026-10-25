"""Causality and timestamp counterexamples using a deterministic toy observer."""
from copy import deepcopy
import unittest
from fixtures.batched_callback_replay.audit import candidate


class Observer:
    def __init__(self,actions=None):
        self.log=[];self.actions=actions or {};self.steps=[]
        self.runtime=self
    def advance(self,event):
        self.steps.append(event['decision_id'])
        for row in event['events']:
            self.log.append(dict(event=row['event'],time_ns=event['now_ns'],callback_ns=row['time_ns']))
        return deepcopy(self.actions.get(event['decision_id'],[]))
    def snapshot(self):
        return dict(runtime=dict(all_events_fired=False),all_events_fired=False,
            events=deepcopy(self.log),processed_steps=self.steps[:])


def entry(identity,now,events=(),nodes=(),drained=False):
    return (dict(decision_id=identity,now_ns=now,events=list(events),drained=drained),
            dict(submissions=list(nodes),finish=False))


def watch(_):return [[dict(operation=2,event='completed')]]


class BatchedCallbackReplayTests(unittest.TestCase):
    def test_batch_processing_keeps_advancement_time_distinct_from_callback_time(self):
        transcript=[entry(0,0),entry(1,5,[dict(operation=2,event='applied',time_ns=2)]),
                    entry(2,9,[dict(operation=2,event='completed',time_ns=7)])]
        result=candidate(Observer(),transcript,watch)
        self.assertIsNone(result['divergence'])
        self.assertEqual(result['physical_flush_count'],2)
        self.assertEqual([row['now_ns'] for row in result['logical_steps']],[0,5,9])
        self.assertEqual([row['time_ns'] for row in result['actual_delivered_snapshot']['events']],[5,9])
        self.assertEqual([row['callback_ns'] for row in result['actual_delivered_snapshot']['events']],[2,7])

    def test_same_nanosecond_batch_boundaries_and_order_survive(self):
        transcript=[entry(0,0),entry(1,5,[dict(operation=2,event='applied',time_ns=5)]),
                    entry(2,5,[dict(operation=2,event='completed',time_ns=5)])]
        result=candidate(Observer(),transcript,watch)
        self.assertEqual(result['physical_flushes'][1]['original_batch_ids'],[1,2])
        self.assertEqual([row['event'] for row in result['actual_delivered_snapshot']['events']],['applied','completed'])

    def test_missing_future_trigger_leaves_tail_and_never_invents_a_live_wakeup(self):
        transcript=[entry(0,0),entry(1,5,[dict(operation=2,event='applied',time_ns=2)])]
        result=candidate(Observer(),transcript,watch)
        self.assertEqual(result['physical_flush_count'],1)
        self.assertEqual(result['pending_tail_original_decisions'],[1])
        self.assertEqual(result['actual_delivered_snapshot']['events'],[])
        self.assertEqual(len(result['reconstructed_snapshot_after_explicit_tail_unfold']['events']),1)
        self.assertTrue(result['logical_steps'][-1]['artificial_tail_unfold'])
        self.assertIsNone(result['logical_steps'][-1]['physical_flush'])
        self.assertFalse(result['representative_four_rank_call_complete'])

    def test_a_needed_earlier_submission_rejects_suppression(self):
        node=dict(id=3,kind='local')
        transcript=[entry(0,0),entry(1,5,[dict(operation=2,event='applied',time_ns=2)],[node])]
        result=candidate(Observer({1:[node]}),transcript,watch)
        self.assertEqual(result['divergence'],dict(kind='suppressed_required_submission_or_finish',original_decision=1))

    def test_reconstructed_node_content_is_checked_against_saved_action(self):
        transcript=[entry(0,0,nodes=[dict(id=0)])]
        result=candidate(Observer(),transcript,watch)
        self.assertEqual(result['divergence']['kind'],'changed_submission_or_finish')

    def test_alarm_or_new_drain_forces_flush_without_predicting_callback(self):
        for record in (entry(1,5,[dict(alarm=0,event='fired',time_ns=5)]),entry(1,5,drained=True)):
            with self.subTest(record=record):
                result=candidate(Observer(),[entry(0,0),record],watch)
                self.assertEqual(result['physical_flush_count'],2)
                self.assertEqual(result['pending_tail_original_decisions'],[])


if __name__=='__main__':unittest.main()
