"""Equivalence, independent closure, strict budgets and hostile saved records."""
from collections import deque
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from moe_ub_cosim import deepep_indexed as indexed
from moe_ub_cosim.deepep_plan import compile_plan, encoded, check as public_check

ROOT = Path(__file__).resolve().parents[2]


def indexed_config(flat):
    return dict(deepcopy(flat), schema_version=indexed.INPUT_SCHEMA, limits=dict(
        max_logical_events=indexed.MAX_EVENTS, max_logical_operations=indexed.MAX_OPERATIONS,
        max_index_bytes=indexed.MAX_INDEX, max_input_bytes=2**20, max_output_bytes=16*2**20))


def decode(events_raw, operations_raw):
    event_rows = [json.loads(line) for line in events_raw.splitlines()]
    op_rows = [dict(zip(indexed.OP_COLUMNS, json.loads(line))) for line in operations_raw.splitlines()]
    events = []
    for identity, kind, rank, deps, operation, attrs in event_rows:
        row = dict(id=identity, kind=kind, rank=rank, deps=[event_rows[d][0] for d in deps])
        if operation is not None:
            row['operation'] = op_rows[operation]['id']
        events.append(dict(row, **attrs))
    for op in op_rows:
        op['events'] = dict(zip(indexed.EVENT_STAGES, (event_rows[i][0] for i in op['events'])))
    return events, op_rows


def independent_blocked(events, held):
    # Forward descendant propagation, unlike generator's dependency-AND scan.
    children = {e['id']:[] for e in events}
    for event in events:
        for parent in event['deps']:
            children[parent].append(event['id'])
    queue, blocked = deque(held), set(held)
    while queue:
        for child in children[queue.popleft()]:
            if child not in blocked:
                blocked.add(child)
                queue.append(child)
    return set(children)-blocked


class IndexedPlanChecks(unittest.TestCase):
    def setUp(self):
        self.flat = json.loads((ROOT/'examples/deepep_partial_order/input.json').read_text())
        self.config = indexed_config(self.flat)

    def streamed(self, config, held=()):
        events, ops = io.BytesIO(), io.BytesIO()
        store = indexed._IndexedStore(config['limits'], indexed._Lines(16*2**20, write=events),
                                      indexed._Lines(16*2**20, write=ops), held)
        from moe_ub_cosim.deepep_plan import _emit_plan
        meta = _emit_plan(config, store)
        decoded = decode(events.getvalue(), ops.getvalue())
        return meta, decoded, store

    def test_exact_flat_equivalence_varied_shapes(self):
        for ranks, experts, capacity, calls, fp8 in [(2, 8, 2, 1, False), (4, 8, 1, 2, False),
                                                    (4, 16, 2, 3, True), (8, 32, 1, 4, False)]:
            config = deepcopy(self.flat)
            config['limits'].update(max_events=65536, max_operations=16384)
            config['model'].update(experts=experts, dispatch_format='fp8_float32_scales' if fp8 else 'bf16')
            config['execution'].update(ranks=ranks, max_tokens_per_rank=capacity)
            config['calls'] = [dict(name=str(i), tokens=[[[2, 3]]*capacity if r%2 else []
                                  for r in range(ranks)]) for i in range(calls)]
            plan = compile_plan(config)
            meta, (events, ops), _ = self.streamed(indexed_config(config))
            self.assertEqual(events, plan['events'])
            self.assertEqual(ops, plan['operations'])
            self.assertEqual(meta['scope'], plan['scope'])
            self.assertEqual(meta['summary'], plan['summary'])

    def test_all_single_event_closures_independent(self):
        # Every one of 728 event withholding choices, not only selected gates.
        _, (events, _), _ = self.streamed(self.config)
        for event in events:
            held = [event['id']]
            _, _, store = self.streamed(self.config, held)
            expected = independent_blocked(events, held)
            self.assertEqual({key for key, i in store.ids.items() if store.done[i]}, expected)
        held = ['m0.d.count.s0.e4.visible', 'm0.c.flag.d1.e0.visible']
        _, _, store = self.streamed(self.config, held)
        self.assertEqual({key for key, i in store.ids.items() if store.done[i]}, independent_blocked(events, held))

    def test_budgets_and_schema_reject_before_directory(self):
        for field, value in [('max_logical_events', 10), ('max_logical_operations', 2),
                             ('max_index_bytes', 64), ('max_output_bytes', 200), ('max_input_bytes', 1),
                             ('max_logical_events', indexed.MAX_EVENTS+1), ('max_index_bytes', True)]:
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config = deepcopy(self.config)
                config['limits'][field] = value
                (root/'input.json').write_bytes(encoded(config))
                with self.assertRaises(ValueError):
                    indexed.execute(root/'input.json', root/'plan')
                self.assertFalse((root/'plan').exists())
        with self.assertRaises(ValueError):
            indexed.validate(self.flat)
        with self.assertRaises(ValueError):
            compile_plan(self.config)

    def test_line_bounds_duplicate_ids_forward_deps(self):
        with self.assertRaises(ValueError):
            indexed._Lines(2**20).emit('x'*indexed.MAX_LINE)
        line = indexed._Lines(2**20, compare=io.BytesIO(b'x'*(indexed.MAX_LINE+1)))
        with self.assertRaises(ValueError):
            line.emit([])
        for rows in ([dict(id='a', kind='begin', rank=0, deps=['future'])],
                     [dict(id='a', kind='begin', rank=0, deps=[])]*2):
            store = indexed._IndexedStore(self.config['limits'], indexed._Lines(2**20), indexed._Lines(2**20), [])
            with self.assertRaises(ValueError):
                for row in rows:
                    store.event(row)
        with self.assertRaises(ValueError):
            indexed._IndexedStore(self.config['limits'], None, None, ['a']*257)

    def test_fresh_artifact_repeat_and_portability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'input.json').write_bytes(encoded(self.config))
            indexed.execute(root/'input.json', root/'first')
            indexed.execute(root/'input.json', root/'second')
            for name in ('input.json', 'plan.json', 'events.jsonl', 'operations.jsonl', 'manifest.json'):
                self.assertEqual((root/'first'/name).read_bytes(), (root/'second'/name).read_bytes())
            result = public_check(root/'first', root/'normal.json')
            self.assertTrue(result['all_kernel_completions_reachable'])
            self.assertEqual(result['event_count'], 728)
            with self.assertRaises(ValueError):
                public_check(root/'first', root/'unknown.json', ['missing'])
            self.assertFalse((root/'unknown.json').exists())
            with self.assertRaises(ValueError):
                indexed.execute(root/'input.json', root/'first')

    def test_rehashed_saved_tamper_extra_line_duplicate_key(self):
        for mode in ('dependency', 'extra', 'duplicate', 'long', 'operation', 'manifest'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root/'input.json').write_bytes(encoded(self.config))
                indexed.execute(root/'input.json', root/'plan')
                directory = root/'plan'
                if mode == 'duplicate':
                    raw = (directory/'input.json').read_bytes()
                    (directory/'input.json').write_bytes(raw.replace(b'{', b'{"schema_version":"bad",', 1))
                else:
                    name = 'operations.jsonl' if mode == 'operation' else 'events.jsonl'
                    path = directory/name
                    lines = path.read_bytes().splitlines(keepends=True)
                    if mode == 'dependency':
                        row = json.loads(lines[8]); row[3] = [999999]; lines[8] = encoded(row)
                    elif mode == 'extra':
                        lines.append(encoded([]))
                    elif mode == 'long':
                        lines[0] = b' '* (indexed.MAX_LINE+1)+b'\n'
                    elif mode == 'operation':
                        row = json.loads(lines[0]); row[8] += 1; lines[0] = encoded(row)
                    path.write_bytes(b''.join(lines))
                    plan = json.loads((directory/'plan.json').read_bytes())
                    plan['files'][name].update(bytes=path.stat().st_size, rows=len(lines),
                                               sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                    manifest = indexed._manifest((directory/'input.json').read_bytes(), plan)
                    if mode == 'manifest':
                        manifest['operation_count'] += 1
                    (directory/'plan.json').write_bytes(encoded(plan))
                    (directory/'manifest.json').write_bytes(encoded(manifest))
                with self.assertRaises(ValueError):
                    public_check(directory, root/'checked.json')
                self.assertFalse((root/'checked.json').exists())


if __name__ == '__main__':
    unittest.main()
