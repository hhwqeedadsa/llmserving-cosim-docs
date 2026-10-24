"""Boundary checks for functional addressed native transactions."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from moe_ub_cosim.scaleout_transactions import validate
ROOT = Path(__file__).resolve().parents[2]


class TransactionInputTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT/'examples/scaleout_rdma_transactions/work.json').read_text())
        self.protocol = json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
        self.topology = dict(schema_version='moe-scaleout-physical-topology/1',
            nodes=[dict(id=i, kind='gpu') for i in range(8)]+[dict(id=8, kind='tor', plane=0)],
            links=[dict(src=i, dst=8, src_port=1, dst_port=i+1, rate_bps=100000000000,delay_ns=1000) for i in range(8)])

    def test_legal_and_non_power_two_depth(self):
        validate(self.topology, self.protocol, self.config)
        c = deepcopy(self.config); c['connections'][0]['queue_depth'] = 3
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_empty_registration_does_not_relax_probe_or_range_checks(self):
        c = deepcopy(self.config); c['operations'] = []
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        validate(self.topology, self.protocol, c, allow_no_remote=True)
        c['windows'][0]['address'] = True
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c, allow_no_remote=True)

    def test_v5_sparse_initialization_and_logical_extent(self):
        c = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-io.json').read_text())
        c['schema_version'] = 'moe-rdma-transactions/5'
        for w in c['windows']:
            data = w.pop('hex'); w.update(bytes=18*2**20, initialization='zero_fill/1', segments=[dict(offset=0, hex=data)])
        validate(self.topology, self.protocol, c)
        for key, value in [('bytes', True), ('bytes', 0), ('bytes', 64*2**20+1),
                           ('initialization', 'unknown'), ('segments', [dict(offset=True, hex='00')]),
                           ('segments', [dict(offset=18*2**20, hex='00')]),
                           ('segments', [dict(offset=1, hex='0000'), dict(offset=2, hex='ff')]),
                           ('segments', [dict(offset=0, hex='00'*32769), dict(offset=40000, hex='00'*32768)])]:
            bad = deepcopy(c); bad['windows'][0][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, bad)
        c['schema_version'] = 'moe-rdma-transactions/4'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_explicit_submission_model_and_typed_prepared_dependency(self):
        c = json.loads((ROOT/'examples/scaleout_rdma_transactions/submission-prefix.json').read_text())
        validate(self.topology, self.protocol, c)
        for mode in ('missing', 'integer', 'local', 'unknown', 'unselected'):
            bad = deepcopy(c)
            if mode == 'missing': del bad['operations'][0]['aggregate_requests']
            elif mode == 'integer': bad['operations'][0]['aggregate_requests'] = 1
            elif mode == 'local': bad['operations'][1]['aggregate_requests'] = True
            elif mode == 'unknown': bad['submission_model']['mode'] = 'unknown'
            else:
                del bad['submission_model']
                for op in bad['operations']: op.pop('aggregate_requests', None)
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, bad)
        bad = deepcopy(self.config); bad['submission_model'] = c['submission_model']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, bad)

    def test_remote_authority_bounds_permissions(self):
        for key, value in [('key', 8), ('address', 4097), ('value', -1), ('value', True), ('value', 2**64)]:
            c = deepcopy(self.config); c['operations'][1][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['windows'][0]['allow_atomic'] = False
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['address'] = 4150
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_future_unknown_events_and_no_guessed_time(self):
        for after in ([dict(operation=5, event='completed')], [dict(operation=0, event='visible')]):
            c = deepcopy(self.config); c['operations'][1]['after'] = after
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['operations'][0]['ready_ns'] = 0
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_protocol_is_explicit_not_legacy_or_overlapping_unordered_puts(self):
        c = deepcopy(self.config); c['operations'][3]['address'] = c['operations'][0]['address']; c['operations'][3]['after'] = []
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['windows'].append(deepcopy(c['windows'][0]))
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(self.config); c['connections'][0]['response_port'] = c['connections'][0]['request_port']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        for key in ('recovery', 'faults', 'admission'):
            protocol = deepcopy(self.protocol); protocol[key] = {}
            with self.assertRaises(ValueError): validate(self.topology, protocol, self.config)

    def test_local_waits_are_owned_bounded_and_typed(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-waits.json').read_text())
        validate(self.topology, self.protocol, original)
        for key, value in [('key', 8), ('address', 4097), ('address', 4153), ('compare', 'gt'),
                           ('value', -1), ('value', True), ('reset_to', 2**64), ('owner', 0)]:
            c = deepcopy(original); c['operations'][3]['local_wait'][0][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        for waits in ([], [None], original['operations'][3]['local_wait'][:1]*2):
            c = deepcopy(original); c['operations'][3]['local_wait'] = waits
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_local_waits_do_not_bypass_existing_write_order_or_depth(self):
        c = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-waits.json').read_text())
        c['operations'][5]['after'] = []
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_queue_fence_selects_only_local_connections(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/queue-fences.json').read_text())
        validate(self.topology, self.protocol, original)
        for selected in ([], [0, 0], [False], [8], [7], '0'):
            c = deepcopy(original); c['operations'][2]['queue_fence'] = selected
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['connections'][1]['src'] = 1
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['operations'][2]['local_wait'] = [dict(key=7,address=4096,compare='eq',value=0)]
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v2_local_nodes_and_typed_event_boundaries(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-stages.json').read_text())
        validate(self.topology, self.protocol, original)
        for key, value in [('rank', True), ('rank', 8), ('duration_ns', True), ('duration_ns', -1),
                           ('duration_ns', 5000001), ('kind', 'compute'), ('ready_ns', 0),
                           ('connection', 0), ('queue_fence', [0])]:
            c = deepcopy(original); c['operations'][3][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['operations'][7]['after'][0]['event'] = 'request_acked'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v2_local_consumption_checks_owned_window(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-stages.json').read_text())
        for key, value in [('value', True), ('reset_to', -1), ('key', 8), ('address', 4097), ('owner', 4)]:
            c = deepcopy(original); c['operations'][4]['local_wait'][0][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v2_model_and_separate_work_budgets(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-stages.json').read_text())
        c = deepcopy(original); c['local_model']['duration_source'] = 'measured'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['schema_version'] = 'moe-rdma-transactions/1'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original)
        for i in range(16):
            c['operations'].append(dict(id=100+i,kind='local',rank=0,duration_ns=0,after=[]))
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v3_source_owner_range_and_exclusive_payload(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/source-windows.json').read_text())
        validate(self.topology, self.protocol, original)
        for key, value in [('key', 8), ('key', True), ('address', 4095), ('address', 5120),
                           ('address', True), ('bytes', 0), ('bytes', True), ('bytes', 32769), ('owner', 0)]:
            c = deepcopy(original); c['operations'][0]['source'][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['operations'][0]['hex'] = '00'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['operations'][1]['source'] = c['operations'][0]['source']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v3_source_model_and_own_reset_are_explicit(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/source-windows.json').read_text())
        c = deepcopy(original); c['source_model']['release'] = 'accepted'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['operations'][0]['local_wait'] = [dict(key=7,address=4096,compare='eq',value=0,reset_to=0)]
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['schema_version'] = 'moe-rdma-transactions/2'; del c['source_model']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)


    def test_v4_owned_copy_ranges_and_nonoverlap(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-io.json').read_text())
        validate(self.topology, self.protocol, original)
        for side, key, value in [('source', 'bytes', True), ('source', 'bytes', 0), ('source', 'bytes', 32769),
                                  ('source', 'key', 7), ('source', 'owner', 4), ('source', 'address', -1),
                                  ('target', 'address', 8192), ('target', 'address', 9600),
                                  ('target', 'address', True), ('target', 'owner', 4), ('target', 'bytes', 257)]:
            c = deepcopy(original); next(o for o in c['operations'] if o['id'] == 1)['copy'][side][key] = value
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)

    def test_v4_model_own_reset_and_old_schema_rejection(self):
        original = json.loads((ROOT/'examples/scaleout_rdma_transactions/local-io.json').read_text())
        for address in [8192, 8576]:
            c = deepcopy(original)
            next(o for o in c['operations'] if o['id'] == 1)['local_wait'] = [dict(key=8,address=address,compare='eq',value=0,reset_to=0)]
            with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['local_memory_model']['capture'] = 'admission/1'
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)
        c = deepcopy(original); c['schema_version'] = 'moe-rdma-transactions/3'; del c['local_memory_model']
        with self.assertRaises(ValueError): validate(self.topology, self.protocol, c)


if __name__ == '__main__': unittest.main()
