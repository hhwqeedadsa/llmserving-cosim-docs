"""Independent source-resource checks, not simulator performance assertions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim.hybrid_deepep_bindings import compile_bindings, execute, load_bindings, manifest
from moe_ub_cosim.hybrid_deepep_plan import execute as logical_plan
from moe_ub_cosim.deepep_plan import encoded

ROOT = Path(__file__).resolve().parents[2]


def example():
    directory = ROOT/'examples/hybrid_bindings'
    return tuple(json.loads((directory/name).read_text()) for name in ('input.json', 'provider.json'))


class HybridBindingTests(unittest.TestCase):
    def test_coverage_and_distinct_self_operations(self):
        config, provider = example()
        bound = compile_bindings(config, provider)
        # Independently expand token -> destination domain from the hand input.
        data = {(ci, src, ti, expert//8) for ci, call in enumerate(config['calls'])
                for src, tokens in enumerate(call['tokens']) for ti, choices in enumerate(tokens) for expert in choices}
        rows = bound['bindings']
        actual = {(r['call'], r['source'], r['token'], r['target']//2)
                  for r in rows if r['kind'] == 'token_data'}
        self.assertEqual(actual, data)
        self.assertEqual(len(rows), 32+len(data)+64)
        resources = {r['id']: r for r in bound['resources']}
        for row in rows:
            same = row['source'] == row['target']
            if row['kind'] in ('rank_count', 'expert_count'):
                self.assertEqual(row['transport'], 'gin')
                self.assertEqual(row['aggregate_requests'], row['kind'] == 'rank_count')
                self.assertEqual(row['context_index'], 0)
            elif row['kind'] == 'token_data':
                self.assertEqual(row['transport'], 'local_copy' if same else 'gin')
                self.assertEqual(row['aggregate_requests'], None if same else True)
            else:
                self.assertEqual(row['transport'], 'local_release_atomic' if same else 'gin')
                self.assertEqual(row['aggregate_requests'], None if same else False)
                self.assertEqual(row['operand_bytes'], 8)
            self.assertEqual(row['resource'] is None, row['transport'] != 'gin')
            if row['resource']:
                q = resources[row['resource']]
                self.assertEqual((q['source'], q['target']), (row['source'], row['target']))
                self.assertEqual(q['provider_peer'], row['target']//2)

    def test_shared_lifetime_and_channel_qp_contention(self):
        config, provider = example()
        provider.update(call_contexts=1, requested_contexts=1)
        bound = compile_bindings(config, provider)
        self.assertEqual(bound['allocated_contexts'], 2)
        self.assertEqual(len(bound['resources']), 8)  # 4 sources x 2 destinations, including count self.
        for src in range(4):
            for dst in (src%2, 2+src%2):
                rows = [r for r in bound['bindings'] if r['source'] == src and r['target'] == dst and r['transport'] == 'gin']
                self.assertEqual(len({r['resource'] for r in rows}), 1)
                self.assertEqual({r['call'] for r in rows}, {0, 1})
                self.assertEqual({r['sharing'] for r in rows}, {'gpu'})
        other = deepcopy(config)
        other['calls'] = other['calls'][:1]
        self.assertEqual(bound['resources'], compile_bindings(other, provider)['resources'])

    def test_context_split_device_rotation_and_peer_translation(self):
        config, provider = example()
        bound = compile_bindings(config, provider)
        self.assertEqual(bound['allocated_contexts'], 4)
        self.assertEqual(len(bound['resources']), 16)
        resources = {r['id']: r for r in bound['resources']}
        # 2 SMs, 2 available data contexts: SM0 uses1, SM1 uses2.
        for row in bound['bindings']:
            if row['transport'] != 'gin':
                continue
            q = resources[row['resource']]
            expected = 0 if row['channel'] is None else 1+row['channel']//2
            self.assertEqual(row['context_index'], expected)
            self.assertEqual(q['provider_connection'], expected%2)
            self.assertEqual(q['local_context'], expected//2)
            self.assertEqual(q['local_device'], provider['local_devices'][row['source']][expected%2])
        provider['gin_connection_stride'] = 1
        world = compile_bindings(config, provider)
        self.assertEqual([r['provider_peer'] for r in world['resources']], [r['world_peer'] for r in world['resources']])
        provider['nic_handler'] = 'gpu_sm_no_dbr'
        self.assertEqual(world, compile_bindings(config, provider))  # Same logical boundaries, costs not modeled.

    def test_sm_local_context_distribution_and_gpu_shared_case(self):
        config, provider = example()
        provider.update(call_contexts=7, requested_contexts=7, connections=3)
        bound = compile_bindings(config, provider)
        self.assertEqual(bound['allocated_contexts'], 9)
        for row in bound['bindings']:
            if row['transport'] == 'gin' and row['channel'] is not None:
                self.assertEqual(row['context_index'], (1, 3, 2, 4)[row['channel']])
                self.assertEqual(row['sharing'], 'cta')
        provider.update(call_contexts=2, requested_contexts=2)
        rows = compile_bindings(config, provider)['bindings']
        for row in rows:
            if row['transport'] == 'gin' and row['channel'] is not None:
                self.assertEqual((row['context_index'], row['sharing']), (1, 'gpu'))

    def test_tail_increments_and_per_peer_prefix_order(self):
        config, provider = example()
        config['execution']['scaleout_warps'] = 1
        for count in (0, 10, 12, 14, 16):
            config['calls'] = [dict(name='tail', tokens=[[[8, 9, 10, 12]]*count, [], [], []])]
            bound = compile_bindings(config, provider)
            table = {r['id']: r for r in bound['bindings']}
            for stream in bound['data_streams']:
                sequence = [table[n] for n in stream['operations']]
                sent, previous = 0, 0
                for row in sequence:
                    if row['kind'] == 'token_data':
                        self.assertEqual(row['domain_channel_slot'], sent)
                        sent += 1
                    else:
                        value = ((row['kind'] == 'finish') << 32) | sent
                        self.assertEqual(row['required_data_prefix'], sent)
                        self.assertEqual(row['add_value'], value-previous)
                        previous = value
                self.assertEqual(sequence[-1]['kind'], 'finish')
                self.assertEqual(previous, (1<<32) | sent)

    def test_reject_unknown_handlers_and_bad_resources_without_writes(self):
        config, provider = example()
        cases = []
        for key, value in (('nic_handler', 'auto'), ('nic_handler', 'cpu_proxy'), ('nic_handler', 'free_flow'),
                           ('backend', 'efa'), ('connections', True), ('requested_contexts', 0),
                           ('gin_connection_stride', True), ('gin_connection_stride', 3), ('extra', 1)):
            bad = deepcopy(provider); bad[key] = value; cases.append((config, bad))
        bad = deepcopy(provider); bad['local_devices'][0] = [10, 10]; cases.append((config, bad))
        bad = deepcopy(provider); bad['local_devices'][0][0] = False; cases.append((config, bad))
        bad = deepcopy(provider); bad['call_contexts'] = 4; cases.append((config, bad))
        for key in ('max_bindings', 'max_resources', 'max_input_bytes', 'max_output_bytes'):
            bad = deepcopy(provider); bad['limits'][key] = 1; cases.append((config, bad))
        bad_config = deepcopy(config); bad_config['execution']['num_sms'] = 1; cases.append((bad_config, provider))
        for logical, bad in cases:
            with self.subTest(logical_sms=logical['execution']['num_sms'], provider=bad):
                with self.assertRaises(ValueError):
                    compile_bindings(logical, bad)

    def test_portability_recompile_and_rehashed_tampering_rejected(self):
        config, provider = example()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'input.json').write_bytes(encoded(config))
            (root/'provider.json').write_bytes(encoded(provider))
            logical_plan(root/'input.json', root/'plan')
            execute(root/'plan', root/'provider.json', root/'bound')
            original = {p.name: p.read_bytes() for p in (root/'bound').iterdir()}
            self.assertEqual(load_bindings(root/'bound')[0], compile_bindings(config, provider))
            execute(root/'plan', root/'provider.json', root/'repeat')
            self.assertEqual(original, {p.name: p.read_bytes() for p in (root/'repeat').iterdir()})
            for field, value in (('resource', None), ('aggregate_requests', False)):
                changed = json.loads(original['bindings.json'])
                changed['bindings'][0][field] = value
                files = {name: raw for name, raw in original.items() if name != 'manifest.json'}
                files['bindings.json'] = encoded(changed)
                for name, raw in files.items():
                    (root/'bound'/name).write_bytes(raw)
                (root/'bound/manifest.json').write_bytes(encoded(manifest(files)))
                with self.assertRaises(ValueError):
                    load_bindings(root/'bound')
            # Illegal declarations must fail before creating an output.
            bad = deepcopy(provider); bad['nic_handler'] = 'auto'
            (root/'bad.json').write_bytes(encoded(bad))
            with self.assertRaises(ValueError):
                execute(root/'plan', root/'bad.json', root/'not-created')
            self.assertFalse((root/'not-created').exists())


if __name__ == '__main__':
    unittest.main()
