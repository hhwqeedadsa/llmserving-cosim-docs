"""Static reuse cannot weaken dynamic admission or alias mutable inputs."""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from moe_ub_cosim.static_stream_validation import StaticStreamValidationCache
from moe_ub_cosim.scaleout_transactions import validate
from moe_ub_cosim.transaction_session import NativeTransactionSession

ROOT = Path(__file__).resolve().parents[2]


class StaticKeyTests(unittest.TestCase):
    def test_exact_copy_hit_detachment_and_nested_mutation(self):
        cache = StaticStreamValidationCache()
        value = dict(nested=[dict(value=1)])
        with patch('moe_ub_cosim.scaleout_streams.validate') as original:
            cache.validate(value, {}, {})
            cache.validate(deepcopy(value), {}, {})
            value['nested'][0]['value'] = 2
            cache.validate(value, {}, {})
            cache.validate(deepcopy(value), {}, {})
        self.assertEqual(original.call_count, 2)
        self.assertEqual(cache.snapshot()['hits'], 2)
        self.assertEqual(cache.snapshot()['entries'], 1)

    def test_boolean_integer_float_and_signed_zero_are_distinct(self):
        with patch('moe_ub_cosim.scaleout_streams.validate') as original:
            cache = StaticStreamValidationCache()
            for value in (True, 1, 1.0, False, 0, 0.0, -0.0):
                cache.validate({'v': value}, {}, {})
            self.assertEqual(original.call_count, 7)
            cache.validate({'v': -0.0}, {}, {})
            self.assertEqual(original.call_count, 7)

    def test_unsupported_types_never_hit_or_use_custom_comparison(self):
        class Unsafe(dict):
            def __eq__(self, other):
                raise AssertionError('user comparison called')
        for value in ((1,), {1: 'value'}, Unsafe(x=1), float('nan'), float('inf'), {1, 2}):
            with self.subTest(kind=type(value)), patch('moe_ub_cosim.scaleout_streams.validate') as original:
                cache = StaticStreamValidationCache()
                cache.validate({'v': value}, {}, {})
                cache.validate({'v': value}, {}, {})
                self.assertEqual(original.call_count, 2)
                self.assertEqual(cache.snapshot()['entries'], 0)
                self.assertEqual(cache.snapshot()['unkeyable'], 2)

    def test_cycle_falls_back_and_does_not_replace_valid_key(self):
        cache = StaticStreamValidationCache()
        cycle = []; cycle.append(cycle)
        with patch('moe_ub_cosim.scaleout_streams.validate') as original:
            cache.validate({}, {}, {})
            cache.validate(cycle, {}, {})
            cache.validate({}, {}, {})
            self.assertEqual(original.call_count, 2)
            self.assertEqual(cache.snapshot()['hits'], 1)

    def test_failed_validation_does_not_replace_successful_key(self):
        cache = StaticStreamValidationCache()
        with patch('moe_ub_cosim.scaleout_streams.validate') as original:
            cache.validate({'v': 1}, {}, {})
            original.side_effect = ValueError('invalid')
            with self.assertRaises(ValueError): cache.validate({'v': 2}, {}, {})
            cache.validate({'v': 1}, {}, {})
            self.assertEqual(original.call_count, 2)

    def test_all_three_inputs_and_independent_caches(self):
        for position in range(3):
            with self.subTest(position=position), patch('moe_ub_cosim.scaleout_streams.validate') as original:
                cache = StaticStreamValidationCache(); args = [{}, {}, {}]
                cache.validate(*args)
                args[position]['changed'] = [1]
                cache.validate(*args)
                StaticStreamValidationCache().validate(*args)
                self.assertEqual(original.call_count, 3)


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_bytes())
        self.protocol = json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_bytes())
        self.topology = dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i, kind='gpu') for i in range(8)]+[dict(id=8, kind='tor', plane=0)],
            links=[dict(src=i, dst=8, src_port=1, dst_port=i+1,
                        rate_bps=100000000000, delay_ns=1000) for i in range(8)])
        self.cache = StaticStreamValidationCache()
        validate(self.topology, self.protocol, self.config, stream_cache=self.cache)

    def check_bad(self, topology, protocol, config):
        with self.assertRaises(ValueError) as full:
            validate(topology, protocol, config)
        with self.assertRaises(ValueError) as cached:
            validate(topology, protocol, config, stream_cache=self.cache)
        self.assertEqual(str(full.exception), str(cached.exception))

    def test_unchanged_static_with_old_operation_mutation_still_rejected(self):
        for change in ('address', 'permission', 'dependency', 'unordered_write', 'duplicate_id'):
            c = deepcopy(self.config)
            if change == 'address': c['operations'][0]['address'] = True
            if change == 'permission': c['windows'][0]['allow_atomic'] = False
            if change == 'dependency': c['operations'][1]['after'] = [dict(operation=65535, event='completed')]
            if change == 'unordered_write':
                c['operations'][3]['address'] = c['operations'][0]['address']; c['operations'][3]['after'] = []
            if change == 'duplicate_id': c['operations'][1]['id'] = c['operations'][0]['id']
            with self.subTest(change=change): self.check_bad(self.topology, self.protocol, c)
        self.assertEqual(self.cache.hits, 5)

    def test_topology_protocol_connection_and_recovery_mutations_revalidate(self):
        for change in ('topology', 'protocol', 'nic', 'recovery', 'type'):
            t, p, c = map(deepcopy, (self.topology, self.protocol, self.config))
            if change == 'topology': t['links'][0]['delay_ns'] = True
            if change == 'protocol': p['payload_bytes'] = True
            if change == 'nic': c['connections'][0]['source_nic'] = 99
            if change == 'recovery': c['transport_recovery'] = dict(mode='unknown')
            if change == 'type': p['payload_bytes'] = float(p['payload_bytes'])
            with self.subTest(change=change): self.check_bad(t, p, c)
        self.assertEqual(self.cache.hits, 0)
        self.assertEqual(self.cache.validations, 6)

    def test_cache_cannot_accept_disguised_object(self):
        with self.assertRaisesRegex(ValueError, 'invalid static'):
            validate(self.topology, self.protocol, self.config, stream_cache=Mock())

    def test_invalid_commit_keeps_pending_config_and_native_pipe(self):
        session = NativeTransactionSession.__new__(NativeTransactionSession)
        session.t, session.p = self.topology, self.protocol
        session.config = deepcopy(self.config)
        session._stream_cache = self.cache; session._alarms = {}; session._delivered = set()
        session.pending = dict(now_ns=0, drained=False)
        session.sequence = 0; session.child = Mock(); session.transcript = io.StringIO()
        before = deepcopy(session.config)
        with self.assertRaises(ValueError): session.commit([deepcopy(self.config['operations'][0])])
        self.assertEqual(session.config, before)
        self.assertEqual(session.sequence, 0)
        self.assertEqual(session.pending, dict(now_ns=0, drained=False))
        session.child.stdin.write.assert_not_called()
        self.assertEqual(self.cache.hits, 1)

    def test_nonboolean_option_rejected_before_io(self):
        for value in (1, None, 'yes'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'must be boolean'):
                NativeTransactionSession(None, None, None, None, None, reuse_static_validation=value)


if __name__ == '__main__': unittest.main()
