"""Provider-stage admission must not fabricate callback or mutate rejected commands."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from moe_ub_cosim.staged_publication import stage_plan
from moe_ub_cosim.transaction_session import NativeTransactionSession, _wake_plan, _group_plan


def program():
    return dict(submission_model=dict(mode='staged_prefix/1'), operations=[
        dict(id=0, kind='remote', aggregate_requests=False),
        dict(id=1, kind='remote', aggregate_requests=True), dict(id=2, kind='local')])


class StagedTests(unittest.TestCase):
    def setUp(self):
        self.empty = dict(prepare=set(), publish=set())
        self.seen = {(0, 'submitted'), (1, 'submitted'), (0, 'prepared'), (1, 'prepared')}

    def test_candidate_copies_lists_and_sets(self):
        rows = [0, 1]
        state, message = stage_plan(program(), self.seen, self.empty, rows, [0])
        rows.clear()
        self.assertEqual(state, dict(prepare={0, 1}, publish={0}))
        self.assertEqual(message['prepare_operations'], [0, 1])
        self.assertEqual(self.empty, dict(prepare=set(), publish=set()))

    def test_bad_identity_type_shape_unknown_local_duplicate_rejected(self):
        for rows in (True, {}, [True], [-1], [65536], ['0'], [[]], [999], [2], [0, 0]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                stage_plan(program(), self.seen, self.empty, rows, None)
        self.assertEqual(self.empty, dict(prepare=set(), publish=set()))

    def test_callbacks_required_from_previous_receive(self):
        for prepare, publish, delivered in (([0], None, set()), (None, [0], {(0, 'submitted')})):
            with self.assertRaisesRegex(ValueError, 'previously delivered'):
                stage_plan(program(), delivered, self.empty, prepare, publish)

    def test_prepare_in_this_command_cannot_authorize_early_ring(self):
        with self.assertRaisesRegex(ValueError, 'prepared'):
            stage_plan(program(), {(0, 'submitted')}, self.empty, [0], [0])

    def test_aggregate_cannot_ring_even_when_prepared(self):
        with self.assertRaisesRegex(ValueError, 'aggregated'):
            stage_plan(program(), self.seen, self.empty, [0], [1])
        self.assertEqual(self.empty['prepare'], set())

    def test_lifetime_duplicate_and_old_modes(self):
        for key in ('prepare', 'publish'):
            prior = deepcopy(self.empty); prior[key].add(0)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                stage_plan(program(), self.seen, prior, [0] if key == 'prepare' else None,
                           [0] if key == 'publish' else None)
        for model in (None, dict(mode='explicit_prefix/1')):
            p = program(); p['submission_model'] = model
            with self.assertRaisesRegex(ValueError, 'require staged'):
                stage_plan(p, self.seen, self.empty, [], None)
            self.assertEqual(stage_plan(p, self.seen, self.empty, None, None)[1], {})

    def test_descriptor_callback_subscription_only_in_staged_mode(self):
        p = program(); p['connections'] = []
        wake = [dict(operation=0, event='descriptor_ready')]
        self.assertEqual(_wake_plan(p, wake, set()), dict(wake_on=wake))
        self.assertEqual(_group_plan(p, [wake], set()), dict(wake_groups=[wake]))
        p['submission_model']['mode'] = 'explicit_prefix/1'
        with self.assertRaises(ValueError): _wake_plan(p, wake, set())

    def test_whole_commit_rejection_keeps_pending_pipe_alarms_and_stages(self):
        session = NativeTransactionSession.__new__(NativeTransactionSession)
        session.config, session.pending = program(), dict(drained=False, now_ns=0)
        session._delivered, session._stage_requested, session._alarms = self.seen, deepcopy(self.empty), {}
        session.child, session.sequence, session.transcript = Mock(), 0, io.StringIO()
        session.p, session.t = dict(schema_version='moe-scaleout-rdma/1'), {}
        original = deepcopy(session.config)
        with patch('moe_ub_cosim.transaction_session.validate'):
            with self.assertRaises(ValueError):
                session.commit([], prepare_operations=[0], publish_operations=[1], alarms=[dict(id=7, delay_ns=1)])
        self.assertEqual(session.config, original)
        self.assertEqual(session._stage_requested, self.empty)
        self.assertEqual(session._alarms, {})
        self.assertIsNotNone(session.pending)
        session.child.stdin.write.assert_not_called()
        self.assertEqual(session.sequence, 0)

    def test_user_example_topology_and_work_validate_together(self):
        from moe_ub_cosim.scaleout_topology import generate
        from moe_ub_cosim.scaleout_transactions import validate
        root=Path(__file__).resolve().parents[2]/'examples/scaleout_staged_publication'
        read=lambda name:json.loads((root/name).read_bytes())
        topology,_=generate(read('topology.json'))
        validate(topology,read('rdma.json'),read('work.json'))

    def test_static_user_entrypoints_reject_before_creating_output_or_launching_backend(self):
        from moe_ub_cosim.scaleout_topology import generate
        from moe_ub_cosim.scaleout_transactions import probe
        from moe_ub_cosim.transaction_session import run
        root=Path(__file__).resolve().parents[2]/'examples/scaleout_staged_publication'
        topology,_=generate(json.loads((root/'topology.json').read_bytes()))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'topology.json').write_text(json.dumps(topology))
            for entry in (probe,run):
                with self.assertRaisesRegex(ValueError,'controller using NativeTransactionSession'):
                    entry(path,root/'rdma.json',root/'work.json',path/'missing-backend',path/'must-not-exist')
                self.assertFalse((path/'must-not-exist').exists())


if __name__ == '__main__': unittest.main()
