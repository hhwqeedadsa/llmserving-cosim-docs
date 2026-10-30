"""Lifecycle semantics; these artificial callbacks do not execute a network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.execution_events import ExecutionObserver, configuration
from moe_ub_cosim.native_control import event
from moe_ub_cosim.service_transport import native_arguments


OPTION = dict(schema_version='moe-execution-events/1', max_events=64)


def wave():
    message = dict(id=7, src=0, dst=1, bytes=256)
    return dict(graph_id='g', messages=[message], rank_nodes={
        '0': [dict(id=1, kind='COMP', deps=[], duration_ns=2),
              dict(id=2, kind='SEND', deps=[1], message=message)],
        '1': [dict(id=3, kind='RECV', deps=[], message=message),
              dict(id=4, kind='COMP', deps=[3], duration_ns=3)]})


def frame(kind, now, **kwargs):
    return dict(schema_version='moe-execution-event/1', type='OBSERVE', kind=kind,
                sequence=0, time_ns=now, **kwargs)


def transcript():
    rows = [frame('node_issue', 0, graph_id='g', rank=0, node=1),
            frame('node_issue', 0, graph_id='g', rank=1, node=3),
            frame('message_recv_post', 0, graph_id='g', rank=1, message=7),
            frame('node_complete', 2, graph_id='g', rank=0, node=1),
            frame('node_issue', 2, graph_id='g', rank=0, node=2),
            frame('message_send_post', 2, graph_id='g', rank=0, message=7),
            frame('message_recv_complete', 8, graph_id='g', rank=1, message=7),
            frame('node_complete', 8, graph_id='g', rank=1, node=3),
            frame('node_issue', 8, graph_id='g', rank=1, node=4),
            frame('message_send_complete', 10, graph_id='g', rank=0, message=7),
            frame('node_complete', 10, graph_id='g', rank=0, node=2),
            frame('node_complete', 11, graph_id='g', rank=1, node=4),
            frame('graph_retired', 11, graph_id='g'), frame('service_drained', 13)]
    for sequence, row in enumerate(rows):
        row['sequence'] = sequence
    return rows


class EventChecks(unittest.TestCase):
    def runtime(self):
        runtime = ExecutionObserver(OPTION)
        runtime.register(wave())
        return runtime

    def test_receiver_ready_does_not_complete_sender_or_retire(self):
        runtime = self.runtime()
        for row in transcript()[:9]:
            runtime.accept(row)
        state = runtime.snapshot()
        self.assertEqual(state['receiver_completions'], 1)
        self.assertEqual(state['sender_completions'], 0)
        self.assertEqual(state['retired_graphs'], 0)
        self.assertIsNone(state['service_drained_ns'])
        for row in transcript()[9:]:
            runtime.accept(row)
        self.assertEqual(runtime.snapshot()['node_completions'], 4)
        self.assertEqual(runtime.drained_ns, 13)

    def test_dependencies_and_duration_and_duplicate_rejected_without_mutation(self):
        runtime = self.runtime()
        runtime.accept(transcript()[0])
        bad = [frame('node_issue', 0, graph_id='g', rank=0, node=2),
               frame('node_complete', 1, graph_id='g', rank=0, node=1),
               frame('node_issue', 1, graph_id='g', rank=0, node=1)]
        before = deepcopy(runtime.__dict__)
        for row in bad:
            row['sequence'] = 1
            with self.subTest(kind=row['kind']), self.assertRaises(ValueError):
                runtime.accept(row)
            self.assertEqual(runtime.__dict__, before)

    def test_source_ack_can_precede_receiver_post(self):
        runtime = self.runtime()
        rows = transcript()
        ordered = [rows[i] for i in (0, 3, 4, 5, 9, 10, 1, 2, 6, 7, 8, 11, 12, 13)]
        for i, row in enumerate(ordered):
            row['sequence'] = i
            row['time_ns'] = max(row['time_ns'], 10 if i >= 6 else 0)
            if i >= 11:row['time_ns'] += 2
            runtime.accept(row)
        self.assertEqual(runtime.messages[7]['send_complete'], 10)
        self.assertEqual(runtime.messages[7]['recv_post'], 10)

    def test_no_fake_completion_on_prefix_or_retirement(self):
        runtime = self.runtime()
        runtime.accept(transcript()[0])
        for row in (frame('graph_retired', 20, graph_id='g'), frame('service_drained', 20)):
            row['sequence'] = 1
            with self.assertRaises(ValueError):runtime.accept(row)
        self.assertIsNone(runtime.drained_ns)
        self.assertEqual(runtime.count, 1)

    def test_message_rank_post_callback_and_node_causality(self):
        for change in ('rank', 'missing-post', 'node-callback'):
            runtime = self.runtime()
            rows = transcript()
            if change == 'rank':rows[2]['rank'] = 0
            elif change == 'missing-post':rows[2]['kind'] = 'message_recv_complete'
            else:rows[6] = dict(rows[7], sequence=6)
            with self.subTest(change=change), self.assertRaises(ValueError):
                for row in rows:runtime.accept(row)

    def test_event_types_fields_time_sequence_and_post_drain(self):
        for field, value in (('kind', 'guess'), ('time_ns', True), ('sequence', 2),
                             ('rank', True), ('graph_id', 'other'), ('node', -1), ('extra', 0)):
            runtime = self.runtime()
            row = dict(transcript()[0], **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):runtime.accept(row)
        runtime = self.runtime()
        for row in transcript():runtime.accept(row)
        with self.assertRaises(ValueError):runtime.accept(transcript()[-1])
        self.assertEqual(runtime.count, 14)

    def test_budget_and_config_and_graph_reuse(self):
        self.assertIsNone(configuration({}))
        for value in (None, {}, dict(OPTION, max_events=True), dict(OPTION, max_events=262145),
                      dict(OPTION, schema_version='future')):
            with self.assertRaises(ValueError):configuration(dict(execution_events=value))
        runtime = ExecutionObserver(dict(OPTION, max_events=13))
        with self.assertRaises(ValueError):runtime.register(wave())
        self.assertFalse(runtime.nodes)
        runtime = self.runtime()
        with self.assertRaises(ValueError):runtime.register(wave())
        for row in transcript()[:3]:runtime.accept(row)
        runtime.config['max_events'] = 3
        with self.assertRaises(ValueError):runtime.accept(transcript()[3])
        self.assertEqual(runtime.count, 3)

    def test_observation_channel_is_explicit_and_one_way(self):
        line = 'MOE_RDMA_EVENT '+json.dumps(transcript()[0])+'\n'
        with self.assertRaises(ValueError):event(line)
        self.assertEqual(event(line, observations=True), transcript()[0])
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(native_arguments({}, Path(folder)), [])
            self.assertEqual(native_arguments(dict(execution_events=OPTION), Path(folder)),
                             ['--execution-events', '64'])


if __name__ == '__main__':unittest.main()
