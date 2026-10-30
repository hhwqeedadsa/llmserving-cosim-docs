"""Contribution coverage and attempt/resource lifetimes; no simulated network."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.contribution_attempts import ContributionAttempts, check


def spec():
    return dict(schema_version='moe-contribution-attempts/1',
        invocation=dict(request_id='request-0', decode_step=1, source_layer=3, token_position=128,
                        execution_sha256='a'*64, input_sha256='b'*64, routing_sha256='c'*64),
        expected_experts=[40, 42, 80], bundles=[dict(id='hot', experts=[40, 42]), dict(id='other', experts=[80])],
        replicas=[dict(rank=20, execution_sha256='a'*64, experts=[40, 41, 42, 43]),
                  dict(rank=21, execution_sha256='a'*64, experts=[40, 41, 42, 43]),
                  dict(rank=40, execution_sha256='a'*64, experts=[80]),
                  dict(rank=41, execution_sha256='a'*64, experts=[80])],
        limits=dict(max_attempts_per_bundle=2, max_resources=16, max_events=128))


def fire(runtime, kind, attempt, time_ns=0, **kwargs):
    return runtime.apply(dict(kind=kind, time_ns=time_ns, attempt=attempt, **kwargs))


def ready(runtime, attempt, bundle='hot', rank=20):
    fire(runtime, 'start', attempt, bundle=bundle, rank=rank)
    for kind in ('input_ready', 'compute_begin', 'compute_done'):
        fire(runtime, kind, attempt)


class Contributions(unittest.TestCase):
    def rejected(self, runtime, event, message):
        before = runtime.snapshot()
        with self.assertRaisesRegex(ValueError, message):
            runtime.apply(event)
        self.assertEqual(runtime.snapshot(), before)

    def test_all_arrival_orders_keep_disjoint_once_only_coverage(self):
        # Four completed providers, two bundles. Same-ns ties use callback order.
        for order in itertools.permutations(('h0', 'h1', 'o0', 'o1')):
            runtime = ContributionAttempts(spec())
            for name, bundle, rank in [('h0', 'hot', 20), ('h1', 'hot', 21), ('o0', 'other', 40), ('o1', 'other', 41)]:
                ready(runtime, name, bundle, rank)
            for name in order:
                key = runtime.bundle_key('hot' if name[0] == 'h' else 'other')
                fire(runtime, 'result', name, 10, key_sha256=key)
            state = runtime.snapshot()
            self.assertEqual(state['accepted_experts'], [40, 42, 80])
            self.assertEqual(state['missing_experts'], [])
            self.assertEqual(state['logical_complete_ns'], 10)
            self.assertEqual(state['winners']['hot']['attempt'], next(x for x in order if x[0] == 'h'))
            self.assertEqual(state['winners']['other']['attempt'], next(x for x in order if x[0] == 'o'))
            self.assertEqual([e['outcome'] for e in state['events']].count('committed'), 2)
            self.assertFalse(state['declared_call_retirable'])

    def test_winner_does_not_ack_close_cancel_or_retire_the_other_attempt(self):
        runtime = ContributionAttempts(spec())
        ready(runtime, 'original')
        fire(runtime, 'resource_open', 'original', resource='original.input-QP')
        ready(runtime, 'backup', rank=21)
        fire(runtime, 'result', 'backup', 10, key_sha256=runtime.bundle_key('hot'))
        state = runtime.snapshot()
        self.assertIsNone(state['resources']['original.input-QP']['closed_ns'])
        self.assertIsNone(state['attempts']['original']['failed_ns'])
        self.assertIsNone(state['attempts']['original']['retired_ns'])
        self.assertEqual(fire(runtime, 'result', 'original', 11, key_sha256=runtime.bundle_key('hot')), 'redundant')
        self.rejected(runtime, dict(kind='retire', time_ns=11, attempt='original'), 'all provider resources')
        fire(runtime, 'resource_close', 'original', 12, resource='original.input-QP')
        fire(runtime, 'retire', 'original', 12)
        self.assertEqual(fire(runtime, 'result', 'original', 13, key_sha256=runtime.bundle_key('hot')), 'late_retired')
        self.assertEqual(runtime.snapshot()['accepted_experts'], [40, 42])

    def test_same_attempt_duplicate_is_not_a_second_result(self):
        runtime = ContributionAttempts(spec())
        ready(runtime, 'a')
        key = runtime.bundle_key('hot')
        self.assertEqual(fire(runtime, 'result', 'a', 0, key_sha256=key), 'committed')
        self.assertEqual(fire(runtime, 'result', 'a', 0, key_sha256=key), 'duplicate')
        self.assertIsNone(runtime.snapshot()['logical_complete_ns'])
        self.assertEqual(runtime.snapshot()['missing_experts'], [80])

    def test_failure_does_not_erase_issued_compute_or_create_a_result(self):
        runtime = ContributionAttempts(spec())
        fire(runtime, 'start', 'a', bundle='hot', rank=20)
        fire(runtime, 'input_ready', 'a')
        fire(runtime, 'compute_begin', 'a')
        fire(runtime, 'fail', 'a', 1, reason='cancelled')
        self.rejected(runtime, dict(kind='retire', time_ns=1, attempt='a'), 'completed in-flight compute')
        fire(runtime, 'compute_done', 'a', 2)
        self.assertEqual(fire(runtime, 'result', 'a', 2, key_sha256=runtime.bundle_key('hot')), 'late_failed')
        fire(runtime, 'retire', 'a', 3)
        state = runtime.snapshot()
        self.assertEqual(state['accepted_experts'], [])
        self.assertEqual(state['attempts']['a']['compute_done_ns'], 2)
        self.assertTrue(state['all_started_attempts_retired'])
        self.assertFalse(state['declared_call_retirable'])

    def test_no_launch_after_failure_but_late_input_can_be_observed(self):
        runtime = ContributionAttempts(spec())
        fire(runtime, 'start', 'a', bundle='hot', rank=20)
        fire(runtime, 'fail', 'a', reason='provider_error')
        fire(runtime, 'input_ready', 'a')
        self.rejected(runtime, dict(kind='compute_begin', time_ns=0, attempt='a'), 'eligible')
        self.rejected(runtime, dict(kind='resource_open', time_ns=0, attempt='a', resource='new'), 'after attempt settlement')

    def test_ineligible_versions_missing_experts_and_wrong_result_identity(self):
        config = spec()
        config['replicas'][1]['execution_sha256'] = 'd'*64
        config['replicas'][3]['experts'] = [79]
        runtime = ContributionAttempts(config)
        self.rejected(runtime, dict(kind='start', time_ns=0, attempt='bad', bundle='hot', rank=21), 'version or expert')
        self.rejected(runtime, dict(kind='start', time_ns=0, attempt='bad', bundle='other', rank=41), 'version or expert')
        ready(runtime, 'a')
        for field, replacement in [('input_sha256', 'e'*64), ('routing_sha256', 'f'*64),
                                   ('source_layer', 4), ('decode_step', 2), ('request_id', 'request-1')]:
            changed = spec()
            changed['invocation'][field] = replacement
            foreign = ContributionAttempts(changed).bundle_key('hot')
            self.rejected(runtime, dict(kind='result', time_ns=1, attempt='a', key_sha256=foreign), 'result identity differs')
        fire(runtime, 'result', 'a', 1, key_sha256=runtime.bundle_key('hot'))
        self.rejected(runtime, dict(kind='fail', time_ns=1, attempt='a', reason='deadline'), 'already settled')

    def test_exact_cover_rejects_overlap_omission_and_addition(self):
        for bundles in ([dict(id='a', experts=[40, 42]), dict(id='b', experts=[42, 80])],
                        [dict(id='a', experts=[40, 42])],
                        [dict(id='a', experts=[40, 42, 80, 81])]):
            config = spec()
            config['bundles'] = bundles
            with self.assertRaises(ValueError):
                ContributionAttempts(config)

    def test_clock_order_and_invalid_callbacks_are_atomic(self):
        runtime = ContributionAttempts(spec())
        fire(runtime, 'start', 'a', 10, bundle='hot', rank=20)
        cases = [dict(kind='input_ready', time_ns=9, attempt='a'),
                 dict(kind='result', time_ns=10, attempt='a', key_sha256=runtime.bundle_key('hot')),
                 dict(kind='compute_done', time_ns=10, attempt='a'),
                 dict(kind='input_ready', time_ns=True, attempt='a'),
                 dict(kind='input_ready', time_ns=10, attempt='a', extra=1),
                 dict(kind='start', time_ns=10, attempt='a', bundle='hot', rank=20)]
        for event in cases:
            self.rejected(runtime, event, 'Contribution attempts:')

    def test_resource_ownership_and_tombstones_survive_closure(self):
        runtime = ContributionAttempts(spec())
        ready(runtime, 'a')
        ready(runtime, 'b', rank=21)
        fire(runtime, 'resource_open', 'a', resource='lease')
        self.rejected(runtime, dict(kind='resource_close', time_ns=0, attempt='b', resource='lease'), 'foreign')
        fire(runtime, 'resource_close', 'a', resource='lease')
        self.rejected(runtime, dict(kind='resource_open', time_ns=0, attempt='b', resource='lease'), 'identity reused')
        self.rejected(runtime, dict(kind='resource_close', time_ns=0, attempt='a', resource='lease'), 'already closed')

    def test_attempt_event_and_resource_budgets_do_not_consume_invalid_events(self):
        config = spec()
        config['limits'].update(max_events=4, max_resources=1, max_attempts_per_bundle=1)
        runtime = ContributionAttempts(config)
        fire(runtime, 'start', 'a', bundle='hot', rank=20)
        self.rejected(runtime, dict(kind='start', time_ns=0, attempt='b', bundle='hot', rank=21), 'attempt budget')
        fire(runtime, 'resource_open', 'a', resource='lease')
        fire(runtime, 'resource_close', 'a', resource='lease')
        self.rejected(runtime, dict(kind='resource_open', time_ns=0, attempt='a', resource='new'), 'resource budget')
        fire(runtime, 'input_ready', 'a')
        self.rejected(runtime, dict(kind='compute_begin', time_ns=0, attempt='a'), 'event budget')

    def test_inputs_and_snapshots_cannot_mutate_active_state(self):
        config = spec()
        runtime = ContributionAttempts(config)
        key = runtime.bundle_key('hot')
        config['invocation']['execution_sha256'] = '0'*64
        config['replicas'][0]['experts'].clear()
        ready(runtime, 'a')
        snapshot = runtime.snapshot()
        snapshot['attempts']['a']['retired_ns'] = 0
        snapshot['events'].clear()
        self.assertIsNone(runtime.snapshot()['attempts']['a']['retired_ns'])
        self.assertEqual(runtime.bundle_key('hot'), key)

    def test_cli_reader_reports_incomplete_valid_transcript_and_preserves_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root/'input.json'
            config.write_text(json.dumps(dict(schema_version='moe-contribution-transcript/1', spec=spec(), events=[])))
            result = check(config, root/'out')
            self.assertTrue(result['transcript_valid'])
            self.assertIsNone(result['logical_complete_ns'])
            self.assertFalse(result['declared_call_retirable'])
            report = (root/'out/report.json').read_bytes()
            with self.assertRaises(FileExistsError):
                check(config, root/'out')
            self.assertEqual((root/'out/report.json').read_bytes(), report)
            config.write_text('{"schema_version":1,"schema_version":2}')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
                check(config, root/'bad')
            self.assertFalse((root/'bad').exists())

    def test_call_retirement_waits_for_both_coverage_and_all_declared_resources(self):
        runtime = ContributionAttempts(spec())
        ready(runtime, 'hot')
        ready(runtime, 'other', bundle='other', rank=40)
        fire(runtime, 'resource_open', 'hot', resource='hot-result-buffer')
        fire(runtime, 'result', 'hot', 1, key_sha256=runtime.bundle_key('hot'))
        fire(runtime, 'result', 'other', 2, key_sha256=runtime.bundle_key('other'))
        fire(runtime, 'retire', 'other', 2)
        self.assertEqual(runtime.snapshot()['logical_complete_ns'], 2)
        self.assertFalse(runtime.snapshot()['declared_call_retirable'])
        fire(runtime, 'resource_close', 'hot', 3, resource='hot-result-buffer')
        fire(runtime, 'retire', 'hot', 4)
        self.assertTrue(runtime.snapshot()['declared_call_retirable'])
        self.assertEqual(runtime.snapshot()['logical_complete_ns'], 2)
        self.rejected(runtime, dict(kind='result', time_ns=5, attempt='hot', key_sha256='0'*64), 'identity differs')
        self.assertEqual(fire(runtime, 'result', 'hot', 5, key_sha256=runtime.bundle_key('hot')), 'late_retired')

    def test_all_attempts_fail_and_retire_without_manufacturing_coverage(self):
        runtime = ContributionAttempts(spec())
        for name, rank in [('original', 20), ('backup', 21)]:
            fire(runtime, 'start', name, bundle='hot', rank=rank)
            fire(runtime, 'fail', name, reason='provider_error')
            fire(runtime, 'retire', name)
        state = runtime.snapshot()
        self.assertIsNone(state['logical_complete_ns'])
        self.assertEqual(state['missing_experts'], [40, 42, 80])
        self.assertTrue(state['all_started_attempts_retired'])
        self.assertFalse(state['declared_call_retirable'])


if __name__ == '__main__':
    unittest.main()
