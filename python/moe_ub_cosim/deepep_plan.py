"""Bounded application partial order for pinned DeepEP low-latency collectives.

This is an offline interface plan, never a packet/GPU/queue-pair simulator.
accepted, visible and sender_done name distinct future backend obligations.
No latency, source buffer release or WQE reservation order is inferred here.
"""
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import shutil

REVISION = '9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'
MAX_INPUT = 2**20
MAX_OUTPUT = 16*2**20


def require(condition, message):
    if not condition:
        raise ValueError('DeepEP plan: ' + message)


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), 'invalid '+label+' fields')


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, 'invalid '+label)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key '+key)
        result[key] = value
    return result


def read(path, maximum):
    require(path.is_file() and path.stat().st_size <= maximum, 'missing or oversized input')
    raw = path.read_bytes()
    require(len(raw) <= maximum, 'oversized input')
    return raw, json.loads(raw, object_pairs_hook=unique)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n').encode()


def validate_config(config):
    fields(config, 'schema_version model execution calls limits', 'root')
    require(config['schema_version'] == 'moe-deepep-ll-plan-input/1', 'unsupported input schema')
    m, x, limits = (config[k] for k in ('model', 'execution', 'limits'))
    fields(m, 'experts top_k hidden_size dispatch_format', 'model')
    integer(m['experts'], 2, 256, 'experts')
    integer(m['top_k'], 1, min(8, m['experts']), 'top_k')
    integer(m['hidden_size'], 128, 8192, 'hidden_size')
    require(m['hidden_size'] % 128 == 0, 'hidden_size must be a multiple of 128')
    require(m['dispatch_format'] in ('bf16', 'fp8_float32_scales'), 'unsupported Dispatch format')
    require(m['dispatch_format'] != 'fp8_float32_scales' or m['hidden_size'] % 512 == 0,
            'FP8 Dispatch requires hidden_size divisible by 512 in the pinned API')
    fields(x, 'ranks api cross_rank_path nics_per_rank max_tokens_per_rank', 'execution')
    integer(x['ranks'], 2, 32, 'ranks')
    integer(x['max_tokens_per_rank'], 1, 16, 'max_tokens_per_rank')
    require(x['ranks']*x['max_tokens_per_rank'] % 4 == 0, 'ranks times max_tokens_per_rank must divide by four')
    require(m['experts'] % x['ranks'] == 0, 'experts must divide evenly among ranks')
    require(x['api'] == 'compute_stream_wait_no_hook' and x['cross_rank_path'] == 'rdma_only',
            'only compute-stream-wait/no-hook, cross-rank RDMA-only application semantics are supported')
    require(type(x['nics_per_rank']) is int and x['nics_per_rank'] == 1, 'only a single NIC is declared')
    fields(limits, 'max_events max_operations max_input_bytes max_output_bytes', 'limits')
    for key, maximum in [('max_events', 65536), ('max_operations', 16384),
                         ('max_input_bytes', MAX_INPUT), ('max_output_bytes', MAX_OUTPUT)]:
        integer(limits[key], 1, maximum, key)
    require(isinstance(config['calls'], list) and 1 <= len(config['calls']) <= 4, 'require 1..4 MoE calls')
    names = set()
    for call in config['calls']:
        fields(call, 'name tokens', 'call')
        require(isinstance(call['name'], str) and 0 < len(call['name']) <= 64 and
                call['name'] not in names, 'missing or repeated call name')
        names.add(call['name'])
        require(isinstance(call['tokens'], list) and len(call['tokens']) == x['ranks'], 'incomplete rank token lists')
        for tokens in call['tokens']:
            require(isinstance(tokens, list) and len(tokens) <= x['max_tokens_per_rank'], 'rank token count exceeds declared buffer layout')
            for choices in tokens:
                require(isinstance(choices, list) and len(choices) == m['top_k'] and
                        all(type(e) is int and 0 <= e < m['experts'] for e in choices) and
                        len(set(choices)) == len(choices), 'invalid expert choices')


class _FlatStore:
    """Historical representation; keep its limits and serialized bytes unchanged."""
    def __init__(self, limits):
        self.limits = limits
        self.events, self.operations, self.seen = [], [], set()

    def check_operation(self):
        require(len(self.operations) < self.limits['max_operations'], 'operation budget exceeded')

    def event(self, row):
        require(row['id'] not in self.seen, 'duplicate event identity')
        require(len(self.events) < self.limits['max_events'], 'event budget exceeded')
        require(all(d in self.seen for d in row['deps']), 'missing or forward event dependency')
        self.events.append(row)
        self.seen.add(row['id'])

    def operation(self, row):
        self.operations.append(row)


def _emit_plan(config, store):
    """Shared semantic construction; storage owns admission and emitted records.

    Callers validate their own representation's input/budgets before entering.
    Neither store may suppress operations or change the dependency relation.
    """
    m, x = (config[k] for k in ('model', 'execution'))
    ranks, experts = x['ranks'], m['experts']
    local_experts = experts//ranks
    summary = Counter()
    # These are partial-order boundaries, not a connected physical QP pool.
    prior_marker, previous_complete = {}, {}

    def event(identity, kind, rank, dependencies, operation=None, **attributes):
        dependencies = sorted(set(dependencies))
        row = dict(id=identity, kind=kind, rank=rank, deps=dependencies)
        if operation is not None:
            row['operation'] = operation
        row.update(attributes)
        store.event(row)
        return identity

    def operation(identity, call, phase, kind, src, dst, expert, operand_bytes,
                  begin, clean=None, data=(), token=None, value=None):
        store.check_operation()
        remote = src != dst
        key = (src, dst, expert % local_experts) if remote else None
        prior = prior_marker.get(key) if remote else None
        issue_deps = [begin] + ([clean] if clean else []) + [d['events']['issued'] for d in data]
        if prior:
            issue_deps.append(prior['events']['issued'])
        issued = event(identity+'.issued', 'issued', src, issue_deps, identity)
        accepted_deps = [issued] + [d['events']['accepted'] for d in data]
        if prior:
            accepted_deps.append(prior['events']['accepted'])
        accepted = event(identity+'.accepted', 'accepted', dst, accepted_deps, identity)
        visible = event(identity+'.visible', 'visible', dst,
                        [accepted]+[d['events']['visible'] for d in data], identity)
        # Transport feedback is not equated to GPU visibility or buffer reuse.
        # No application successor below depends on this event. A backend must
        # separately add actual queue/resource constraints before execution.
        done = event(identity+'.sender_done', 'sender_done', src, [accepted], identity)
        row = dict(id=identity, call=call, phase=phase, kind=kind, src=src, dst=dst,
                   expert=expert, token=token, operand_bytes=operand_bytes, value=value,
                   contributions=1 if kind == 'data' else len(data),
                   path='rdma' if remote else 'local',
                   verb=('rdma_write' if kind == 'data' else 'rdma_atomic_add32') if remote else
                        ('local_copy' if kind == 'data' else 'local_store_release32'),
                   qp_key=list(key) if key else None,
                   buffer_epoch=2*call+(phase == 'combine'),
                   buffer_index=0 if phase == 'dispatch' else 1,
                   events=dict(issued=issued, accepted=accepted, visible=visible, sender_done=done))
        store.operation(row)
        summary[row['path']+'_'+kind+'_operations'] += 1
        summary[row['path']+'_'+kind+'_operand_bytes'] += operand_bytes
        if kind == 'count' and value == -1:
            summary[row['path']+'_zero_token_count_operations'] += 1
        if kind == 'flag' and row['contributions'] == 0:
            summary[row['path']+'_zero_token_flag_operations'] += 1
        return row

    for ci, call in enumerate(config['calls']):
        count, dispatch_data, combine_data = Counter(), {}, {}
        for src, tokens in enumerate(call['tokens']):
            for ti, choices in enumerate(tokens):
                for e in choices:
                    count[src, e] += 1
        start = {r:event(f'm{ci}.d.r{r}.begin', 'kernel_begin', r,
                         [previous_complete[r]] if ci else []) for r in range(ranks)}
        clean = {r:event(f'm{ci}.d.r{r}.clean_next', 'clean_next_buffer', r, [start[r]],
                         buffer_index=1, next_kernel_epoch=2*ci+1) for r in range(ranks)}
        dispatch_bytes = 2*m['hidden_size']+16 if m['dispatch_format'] == 'bf16' else m['hidden_size']+4*(m['hidden_size']//128)+16
        for src, tokens in enumerate(call['tokens']):
            for ti, choices in enumerate(tokens):
                for e in choices:
                    op = operation(f'm{ci}.d.data.s{src}.t{ti}.e{e}', ci, 'dispatch', 'data',
                                   src, e//local_experts, e, dispatch_bytes, start[src], token=ti)
                    dispatch_data.setdefault((src, e), []).append(op)
        notifications = {}
        for src in range(ranks):
            for e in range(experts):
                op = operation(f'm{ci}.d.count.s{src}.e{e}', ci, 'dispatch', 'count',
                               src, e//local_experts, e, 4, start[src], clean[src],
                               dispatch_data.get((src, e), ()), value=-count[src, e]-1)
                notifications[src, e] = op
                if op['qp_key']:
                    prior_marker[tuple(op['qp_key'])] = op
        dispatch_complete = {}
        for rank in range(ranks):
            sent = event(f'm{ci}.d.r{rank}.send_issued', 'send_phase_issued', rank,
                         [notifications[rank, e]['events']['issued'] for e in range(experts)])
            packed = event(f'm{ci}.d.r{rank}.consume_done', 'dispatch_packed', rank,
                           [start[rank]]+[notifications[s, e]['events']['visible'] for s in range(ranks)
                            for e in range(rank*local_experts, (rank+1)*local_experts)])
            dispatch_complete[rank] = event(f'm{ci}.d.r{rank}.complete', 'kernel_complete', rank, [sent, packed])
        # Expert work is an untimed causal stage, not a GPU performance estimate.
        expert_done = {r:event(f'm{ci}.r{r}.experts_done', 'expert_stage_done', r,
                              [dispatch_complete[r]]) for r in range(ranks)}
        start = {r:event(f'm{ci}.c.r{r}.begin', 'kernel_begin', r, [expert_done[r]]) for r in range(ranks)}
        clean = {r:event(f'm{ci}.c.r{r}.clean_next', 'clean_next_buffer', r, [start[r]],
                         buffer_index=0, next_kernel_epoch=2*ci+2) for r in range(ranks)}
        for original, tokens in enumerate(call['tokens']):
            for ti, choices in enumerate(tokens):
                for e in choices:
                    owner = e//local_experts
                    op = operation(f'm{ci}.c.data.d{original}.t{ti}.e{e}', ci, 'combine', 'data',
                                   owner, original, e, 2*m['hidden_size'], start[owner], token=ti)
                    combine_data.setdefault((original, e), []).append(op)
        flags = {}
        for original in range(ranks):
            for e in range(experts):
                owner = e//local_experts
                op = operation(f'm{ci}.c.flag.d{original}.e{e}', ci, 'combine', 'flag',
                               owner, original, e, 4, start[owner], clean[owner],
                               combine_data.get((original, e), ()), value=1)
                flags[original, e] = op
                if op['qp_key']:
                    prior_marker[tuple(op['qp_key'])] = op
        for rank in range(ranks):
            sent = event(f'm{ci}.c.r{rank}.send_issued', 'send_phase_issued', rank,
                         [flags[d, e]['events']['issued'] for d in range(ranks)
                          for e in range(rank*local_experts, (rank+1)*local_experts)])
            merged = event(f'm{ci}.c.r{rank}.consume_done', 'combine_merged', rank,
                           [start[rank]]+[flags[rank, e]['events']['visible'] for e in range(experts)])
            previous_complete[rank] = event(f'm{ci}.c.r{rank}.complete', 'kernel_complete', rank, [sent, merged])
    return dict(schema_version='moe-deepep-ll-partial-order/1',
        source=dict(library='DeepEP', revision=REVISION, path='csrc/kernels/internode_ll.cu'),
        scope=dict(network_executed=False, cuda_executed=False, latency_predicted=False,
                   hardware_calibrated=False, numerical_experts_executed=False,
                   source_buffer_reuse_validated=False, kernel_shape_support_validated=False,
                   qp_key='logical single-NIC peer/local-expert context; no physical QP created',
                   sender_done='required future transport callback; no NIC queue or CQ execution',
                   intra_phase_data_order='runtime WQE reservation order not fixed',
                   unknowns=['WQE/key splitting', 'CQ and queue depth backpressure', 'retries and buffer lifetime',
                             'GPU visibility/packing cost', 'physical NIC/ECMP mapping']),
        calls=len(config['calls']), ranks=ranks, summary=dict(summary))


def compile_plan(config):
    validate_config(config)
    store = _FlatStore(config['limits'])
    metadata = _emit_plan(config, store)
    summary = metadata.pop('summary')
    return dict(**metadata, operations=store.operations, events=store.events, summary=summary)


def reachable(plan, withheld=()):
    """Untimed dependency closure. Withholding is a logic probe, never loss injection."""
    nodes = {n['id']:n for n in plan['events']}
    require(len(nodes) == len(plan['events']), 'duplicate saved event')
    held = set(withheld)
    require(held <= nodes.keys(), 'unknown withheld event')
    waiting, successors = {}, {n:[] for n in nodes}
    for identity, node in nodes.items():
        require(len(node['deps']) == len(set(node['deps'])) and all(d in nodes for d in node['deps']), 'invalid dependencies')
        waiting[identity] = len(node['deps'])
        for parent in node['deps']:
            successors[parent].append(identity)
    ready = deque(n for n in nodes if waiting[n] == 0 and n not in held)
    done = set()
    while ready:
        identity = ready.popleft()
        done.add(identity)
        for child in successors[identity]:
            waiting[child] -= 1
            if waiting[child] == 0 and child not in held:
                ready.append(child)
    return done


def execute(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    raw, config = read(config_path, MAX_INPUT)
    validate_config(config)
    require(len(raw) <= config['limits']['max_input_bytes'], 'declared input budget exceeded')
    require(not output.exists() and output.parent.is_dir(), 'output must be a new directory with an existing parent')
    require(shutil.disk_usage(output.parent).free >= 8*2**30+MAX_OUTPUT, 'output volume must reserve 8GiB')
    plan = compile_plan(config)
    require(len(reachable(plan)) == len(plan['events']), 'generated event graph is cyclic')
    plan_raw = encoded(plan)
    manifest = dict(schema_version='moe-deepep-ll-plan-manifest/1',
                    input_sha256=hashlib.sha256(raw).hexdigest(), plan_sha256=hashlib.sha256(plan_raw).hexdigest(),
                    event_count=len(plan['events']), operation_count=len(plan['operations']),
                    plan_bytes=len(plan_raw), state='offline_partial_order_only',
                    simulated_packets=False, summary=plan['summary'])
    manifest_raw = encoded(manifest)
    total_bytes = len(raw)+len(plan_raw)+len(manifest_raw)
    require(total_bytes <= config['limits']['max_output_bytes'], 'declared total output budget exceeded')
    output.mkdir()
    (output/'input.json').write_bytes(raw)
    (output/'plan.json').write_bytes(plan_raw)
    (output/'manifest.json').write_bytes(manifest_raw)
    return dict(passed=True, output=str(output), total_output_bytes=total_bytes, **manifest)


def check(directory, output, withheld=()):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    require(not output.exists() and output.parent.is_dir(), 'check output must be a new file with an existing parent')
    _, kind = read(directory/'manifest.json', MAX_INPUT)
    if kind.get('schema_version') == 'moe-deepep-ll-indexed-manifest/1':
        from .deepep_indexed import check as indexed_check
        return indexed_check(directory, output, withheld)
    raw, plan = read(directory/'plan.json', MAX_OUTPUT)
    original, config = read(directory/'input.json', MAX_INPUT)
    manifest_raw, manifest = read(directory/'manifest.json', MAX_INPUT)
    require(manifest['schema_version'] == 'moe-deepep-ll-plan-manifest/1', 'unsupported manifest schema')
    require(hashlib.sha256(raw).hexdigest() == manifest['plan_sha256'] and
            hashlib.sha256(original).hexdigest() == manifest['input_sha256'], 'saved input/plan hash mismatch')
    # Regeneration checks saved representation against this compiler version;
    # it is not an independent source/hardware validation.
    require(encoded(compile_plan(config)) == raw, 'saved plan differs from current compiler')
    require(len(raw)+len(original)+len(manifest_raw) <= config['limits']['max_output_bytes']
            and len(original) <= config['limits']['max_input_bytes'],
            'saved input/output exceeds declared budget')
    require(manifest['event_count'] == len(plan['events']) and manifest['operation_count'] == len(plan['operations'])
            and manifest['plan_bytes'] == len(raw) and manifest['summary'] == plan['summary'], 'manifest count/summary mismatch')
    all_done = reachable(plan)
    require(len(all_done) == len(plan['events']), 'saved graph is cyclic')
    done = reachable(plan, withheld)
    returns = [e['id'] for e in plan['events'] if e['kind'] == 'kernel_complete']
    result = dict(passed=True, scope='representation integrity and untimed dependency closure only',
                  withheld=sorted(set(withheld)), event_count=len(plan['events']), reachable_events=len(done),
                  all_kernel_completions_reachable=all(r in done for r in returns),
                  blocked_kernel_completions=[r for r in returns if r not in done],
                  reachable_kernel_completions=[r for r in returns if r in done],
                  simulated_packets=False, latency_predicted=False,
                  source_buffer_reuse_validated=False, plan_sha256=manifest['plan_sha256'])
    output.write_bytes(encoded(result))
    return result
