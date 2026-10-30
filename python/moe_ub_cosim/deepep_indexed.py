"""Stream the pinned legacy partial order with integer references, never packets.

No full event/operation list or successor graph is retained. Event ID lookup,
one closure byte per event and the semantic generator's phase state remain
bounded independently of the serialized byte budget.
"""
from contextlib import ExitStack
import hashlib
from pathlib import Path
import shutil
import sys

from .deepep_plan import (MAX_INPUT, MAX_OUTPUT, _emit_plan, encoded, fields,
                          integer, read, require, validate_config)

INPUT_SCHEMA = 'moe-deepep-ll-indexed-input/1'
PLAN_SCHEMA = 'moe-deepep-ll-indexed-plan/1'
MANIFEST_SCHEMA = 'moe-deepep-ll-indexed-manifest/1'
# Derived from the unchanged shape limits: 4 calls, 32 ranks, 256 experts,
# 16 tokens/rank, Top8. These are logical counts, not flat materialization caps.
MAX_OPERATIONS = 4*(2*32*256+2*32*16*8)
MAX_EVENTS = 4*MAX_OPERATIONS+11*32*4
MAX_INDEX = 64*2**20
MAX_LINE = 64*2**10
OP_COLUMNS = ('id', 'call', 'phase', 'kind', 'src', 'dst', 'expert', 'token',
              'operand_bytes', 'value', 'contributions', 'path', 'verb',
              'qp_key', 'buffer_epoch', 'buffer_index', 'events')
EVENT_COLUMNS = ('id', 'kind', 'rank', 'deps', 'operation', 'attributes')
EVENT_STAGES = ('issued', 'accepted', 'visible', 'sender_done')


def validate(config):
    fields(config, 'schema_version model execution calls limits', 'indexed root')
    require(config['schema_version'] == INPUT_SCHEMA, 'unsupported indexed input schema')
    limits = config['limits']
    fields(limits, 'max_logical_events max_logical_operations max_index_bytes max_input_bytes max_output_bytes',
           'indexed limits')
    for name, maximum in [('max_logical_events', MAX_EVENTS),
                          ('max_logical_operations', MAX_OPERATIONS),
                          ('max_index_bytes', MAX_INDEX), ('max_input_bytes', MAX_INPUT),
                          ('max_output_bytes', MAX_OUTPUT)]:
        integer(limits[name], 1, maximum, name)
    # Reuse only the established model/API/shape validation; never invoke the
    # old materializer with raised caps or silently change its input schema.
    validate_config(dict(config, schema_version='moe-deepep-ll-plan-input/1',
        limits=dict(max_events=65536, max_operations=16384,
                    max_input_bytes=MAX_INPUT, max_output_bytes=MAX_OUTPUT)))
    operations = sum(2*config['execution']['ranks']*config['model']['experts']+
                     2*sum(len(choices) for tokens in call['tokens'] for choices in tokens)
                     for call in config['calls'])
    events = 4*operations+11*config['execution']['ranks']*len(config['calls'])
    require(operations <= limits['max_logical_operations'], 'logical operation budget exceeded')
    require(events <= limits['max_logical_events'], 'logical event budget exceeded')
    return operations, events


class _Lines:
    def __init__(self, limit, *, write=None, compare=None):
        self.limit, self.write, self.compare = limit, write, compare
        self.bytes = self.rows = 0
        self.hash = hashlib.sha256()

    def emit(self, value):
        raw = encoded(value)
        require(len(raw) <= MAX_LINE, 'indexed record line budget exceeded')
        require(self.bytes+len(raw) <= self.limit, 'indexed record output budget exceeded')
        if self.compare is not None:
            # Bound reads even if an attacker replaces one line by a huge file.
            saved = self.compare.readline(MAX_LINE+1)
            require(len(saved) <= MAX_LINE, 'saved indexed line budget exceeded')
            require(saved == raw, 'saved indexed record differs from current compiler')
        if self.write is not None:
            self.write.write(raw)
        self.hash.update(raw)
        self.bytes += len(raw)
        self.rows += 1

    def finish(self):
        require(self.compare is None or self.compare.read(1) == b'', 'extra saved indexed records')
        return dict(bytes=self.bytes, rows=self.rows, sha256=self.hash.hexdigest())


class _IndexedStore:
    def __init__(self, limits, events, operations, withheld):
        self.limits, self.events, self.operations = limits, events, operations
        require(len(withheld) <= 256 and all(isinstance(w, str) and len(w) <= 256 for w in withheld),
                'at most 256 bounded withheld event IDs are supported')
        self.held = set(withheld)
        self.ids, self.done = {}, bytearray()
        self.key_bytes = self.peak_index_bytes = 0
        self.pending = None
        self.reachable_count = 0
        self.returns, self.blocked = [], []

    def check_operation(self):
        require(self.operations.rows < self.limits['max_logical_operations'], 'logical operation budget exceeded')

    def event(self, row):
        identity, index = row['id'], self.events.rows
        require(index < self.limits['max_logical_events'], 'logical event budget exceeded')
        require(identity not in self.ids, 'duplicate event identity')
        require(all(d in self.ids for d in row['deps']), 'missing or forward event dependency')
        deps = [self.ids[d] for d in row['deps']]
        ready = identity not in self.held and all(self.done[d] for d in deps)
        self.ids[identity] = index
        self.done.append(ready)
        self.key_bytes += sys.getsizeof(identity)+sys.getsizeof(index)
        size = sys.getsizeof(self.ids)+self.key_bytes+sys.getsizeof(self.done)
        require(size <= self.limits['max_index_bytes'], 'indexed lookup memory budget exceeded')
        self.peak_index_bytes = max(size, self.peak_index_bytes)
        self.reachable_count += ready
        op = row.get('operation')
        if op is not None:
            require(self.pending in (None, op), 'operation events must remain contiguous')
            self.pending = op
        attrs = {k:v for k, v in row.items() if k not in ('id', 'kind', 'rank', 'deps', 'operation')}
        self.events.emit([identity, row['kind'], row['rank'], deps,
                          None if op is None else self.operations.rows, attrs])
        if row['kind'] == 'kernel_complete':
            (self.returns if ready else self.blocked).append(identity)

    def operation(self, row):
        require(self.pending == row['id'], 'missing indexed operation events')
        values = dict(row, events=[self.ids[row['events'][k]] for k in EVENT_STAGES])
        self.operations.emit([values[k] for k in OP_COLUMNS])
        self.pending = None


def _scan(config, withheld=(), *, directory=None, mode=None):
    limit = config['limits']['max_output_bytes']
    with ExitStack() as stack:
        streams = {}
        for name in ('events.jsonl', 'operations.jsonl'):
            stream = None if directory is None else stack.enter_context((directory/name).open(mode))
            streams[name] = _Lines(limit, **({'compare':stream} if mode == 'rb' else {'write':stream}))
        store = _IndexedStore(config['limits'], streams['events.jsonl'], streams['operations.jsonl'], withheld)
        meta = _emit_plan(config, store)
        require(store.held <= store.ids.keys(), 'unknown withheld event')
        require(store.pending is None, 'incomplete indexed operation')
        files = {name:stream.finish() for name, stream in streams.items()}
        require(sum(f['bytes'] for f in files.values()) <= limit, 'indexed records total output budget exceeded')
        scope = meta.pop('scope')
        meta.pop('schema_version')
        plan = dict(schema_version=PLAN_SCHEMA, semantic_schema='moe-deepep-ll-partial-order/1',
                    **meta, scope=scope,
                    operation_columns=list(OP_COLUMNS), event_columns=list(EVENT_COLUMNS),
                    operation_event_stages=list(EVENT_STAGES), reference_base=0,
                    files=files, storage=dict(full_event_array_retained=False, full_operation_array_retained=False,
                        storage_sink_records_retained=0,
                        event_id_index_retained=True, closure_bytes_per_event=1,
                        record_line_max_bytes=MAX_LINE, phase_state_retained=True))
        closure = dict(event_count=streams['events.jsonl'].rows,
            operation_count=streams['operations.jsonl'].rows,
            reachable_events=store.reachable_count,
            all_kernel_completions_reachable=not store.blocked,
            blocked_kernel_completions=store.blocked, reachable_kernel_completions=store.returns,
            peak_accounted_index_bytes=store.peak_index_bytes)
        return plan, closure


def _manifest(original, plan):
    raw = encoded(plan)
    return dict(schema_version=MANIFEST_SCHEMA, input_sha256=hashlib.sha256(original).hexdigest(),
        plan_sha256=hashlib.sha256(raw).hexdigest(), plan_bytes=len(raw), files=plan['files'],
        operation_count=plan['files']['operations.jsonl']['rows'],
        event_count=plan['files']['events.jsonl']['rows'], summary=plan['summary'],
        state='offline_partial_order_only', simulated_packets=False)


def _total(original, plan, manifest, limits):
    total = len(original)+len(encoded(plan))+len(encoded(manifest))+sum(f['bytes'] for f in plan['files'].values())
    require(len(original) <= limits['max_input_bytes'], 'declared indexed input budget exceeded')
    require(total <= limits['max_output_bytes'], 'declared indexed total output budget exceeded')
    return total


def execute(config_path, output):
    output = Path(output).resolve()
    original, config = read(Path(config_path).resolve(), MAX_INPUT)
    validate(config)
    require(len(original) <= config['limits']['max_input_bytes'], 'declared indexed input budget exceeded')
    require(not output.exists() and output.parent.is_dir(), 'output must be a new directory with an existing parent')
    require(shutil.disk_usage(output.parent).free >= 8*2**30+MAX_OUTPUT, 'output volume must reserve 8GiB')
    plan, closure = _scan(config)
    require(closure['reachable_events'] == closure['event_count'], 'generated indexed graph is cyclic')
    manifest = _manifest(original, plan)
    total = _total(original, plan, manifest, config['limits'])
    # All deterministic admission checks run before creating any output.
    output.mkdir()
    written, written_closure = _scan(config, directory=output, mode='xb')
    require(written == plan and written_closure == closure, 'indexed write differs from preflight')
    (output/'input.json').write_bytes(original)
    (output/'plan.json').write_bytes(encoded(plan))
    # Manifest is written last; an interrupted directory is not accepted.
    (output/'manifest.json').write_bytes(encoded(manifest))
    return dict(passed=True, output=str(output), total_output_bytes=total, **manifest,
                peak_accounted_index_bytes=closure['peak_accounted_index_bytes'])


def check(directory, output, withheld=()):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    require(not output.exists() and output.parent.is_dir(), 'check output must be a new file with an existing parent')
    original, config = read(directory/'input.json', MAX_INPUT)
    validate(config)
    plan_raw, saved = read(directory/'plan.json', MAX_INPUT)
    manifest_raw, manifest = read(directory/'manifest.json', MAX_INPUT)
    fields(manifest, 'schema_version input_sha256 plan_sha256 plan_bytes files operation_count event_count summary state simulated_packets',
           'indexed manifest')
    require(manifest['schema_version'] == MANIFEST_SCHEMA, 'unsupported indexed manifest schema')
    require(hashlib.sha256(original).hexdigest() == manifest['input_sha256'] and
            hashlib.sha256(plan_raw).hexdigest() == manifest['plan_sha256'], 'saved indexed input/plan hash mismatch')
    # The fixed filenames, cumulative file size, canonical generated line and
    # per-file hash are checked; no saved path or dependency is executed.
    require(set(manifest['files']) == {'events.jsonl', 'operations.jsonl'}, 'invalid indexed files')
    for name in manifest['files']:
        require((directory/name).is_file() and (directory/name).stat().st_size <= config['limits']['max_output_bytes'],
                'missing or oversized indexed records')
    require(len(original)+len(plan_raw)+len(manifest_raw)+sum((directory/n).stat().st_size for n in manifest['files'])
            <= config['limits']['max_output_bytes'], 'saved indexed total output budget exceeded')
    regenerated, closure = _scan(config, withheld, directory=directory, mode='rb')
    require(encoded(regenerated) == plan_raw and _manifest(original, regenerated) == manifest,
            'saved indexed metadata differs from current compiler')
    _total(original, regenerated, manifest, config['limits'])
    result = dict(passed=True, scope='representation integrity and untimed dependency closure only',
                  withheld=sorted(set(withheld)), **closure, simulated_packets=False, latency_predicted=False,
                  source_buffer_reuse_validated=False, plan_sha256=manifest['plan_sha256'])
    output.write_bytes(encoded(result))
    return result
