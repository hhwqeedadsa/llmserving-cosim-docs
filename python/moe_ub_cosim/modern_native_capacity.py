"""Untimed complete-workload requirements for the functional modern bridge.

This is a static shape audit, not physical admission, a callback simulation or
a predictor of runtime memory/time. In particular, possible connections are
not necessarily used and initialized bytes are not transmitted bytes.
"""
from collections import Counter
from pathlib import Path

from .deepep_plan import encoded, require
from .modern_deepep_plan import compile_plan, resolve_combine_channel
from .modern_native_wakeups import LOCAL_EVENTS
from .scaleout_rdma import checked_json, digest


NATIVE_RANK_LIMIT = 4
NATIVE_CONNECTION_LIMIT = 64
NATIVE_REMOTE_LIMIT = 1024
NATIVE_LOCAL_LIMIT = 256


def connection_keys(plan, execution):
    """Union of actual and possible dynamic-slot contexts; no slot is chosen."""
    keys = set()
    for op in plan['operations']:
        if op['path'] == 'local':
            continue
        if op['phase'] == 'combine' and op['kind'] == 'token_data':
            for slot in range(op['binding']['source_to_owner_count']):
                binding = resolve_combine_channel(op['binding'], slot, execution, op['src'])
                keys.add((op['src'], binding['context_index'], op['dst']))
        else:
            keys.add((op['src'], op['binding']['context_index'], op['dst']))
    return sorted(keys)


def node_counts(plan):
    """Exact counts if all modern events finish; no callback order assumed."""
    paths = Counter(op['path'] for op in plan['operations'])
    events = Counter(e['kind'] for e in plan['events'] if e['kind'] in LOCAL_EVENTS)
    preparations = len({(op['call'], op['src']) for op in plan['operations']
                        if op['kind'] in ('rank_count', 'expert_count_block')})
    return dict(remote=len(plan['operations'])-paths['local'],
                local=paths['local']+sum(events.values())+preparations,
                local_operations=paths['local'], local_events=dict(sorted(events.items())),
                count_preparations=preparations, paths=dict(sorted(paths.items())))


def require_node_capacity(plan):
    counts = node_counts(plan)
    require(counts['remote'] <= NATIVE_REMOTE_LIMIT,
            f"complete modern workload requires {counts['remote']} remote operations; limit {NATIVE_REMOTE_LIMIT}")
    require(counts['local'] <= NATIVE_LOCAL_LIMIT,
            f"complete modern workload requires {counts['local']} local operations; limit {NATIVE_LOCAL_LIMIT}")


def requirements(config):
    plan = compile_plan(config)
    ranks = config['execution']['ranks']
    keys = connection_keys(plan, config['execution'])
    nodes = node_counts(plan)
    count_bytes = 8*(ranks+config['model']['experts'])
    # Replay only immutable allocation arithmetic; no runtime/event scheduling.
    offsets, segments, initial = [0]*ranks, [0]*ranks, [0]*ranks

    def allocate(rank, size, initialized):
        offsets[rank] = (offsets[rank]+63)//64*64+size
        segments[rank] += bool(initialized)
        initial[rank] += initialized

    for rank in range(ranks):
        allocate(rank, count_bytes, 0)  # Explicit all-zero count reset source.
    for _ in config['calls']:
        for rank in range(ranks):
            allocate(rank, count_bytes, count_bytes)
    seen = set()
    for op in plan['operations']:
        if op['kind'] != 'token_data':
            continue
        key = ('dispatch', op['call'], op['src'], op['token']) if op['phase'] == 'dispatch' else op['id']
        if key not in seen:
            allocate(op['src'], op['operand_bytes'], 32)
            seen.add(key)
    devices = {rank: set() for rank in range(ranks)}
    for src, context, dst in keys:
        connection = context % config['execution']['connections']
        for rank in (src, dst):
            table = config['execution']['local_devices'][rank]
            devices[rank].add(table[connection % len(table)])
    # A physical deployment can alias different logical device ids to one NIC.
    # Consequently 2*rank is a lower bound, not an exact registration count.
    bound = 2*len({r for src, _, dst in keys for r in (src, dst)})
    checks = []

    def check(name, needed, maximum, source, kind='exact'):
        checks.append(dict(name=name, required=needed, maximum=maximum,
                           requirement_kind=kind, passed=needed <= maximum, enforced_by=source))

    check('logical_ranks', ranks, NATIVE_RANK_LIMIT, 'modern_native_layout.py')
    check('canonical_plan_bytes', len(encoded(plan)), config['limits']['max_output_bytes'], 'modern_native_layout.py')
    check('canonical_input_bytes', len(encoded(config)), config['limits']['max_input_bytes'], 'modern_native_layout.py')
    check('prospective_connections', len(keys), NATIVE_CONNECTION_LIMIT, 'modern_native_layout.py; transaction_limits.hh')
    check('remote_nodes', nodes['remote'], NATIVE_REMOTE_LIMIT, 'modern_native.py; transaction_limits.hh')
    check('local_nodes', nodes['local'], NATIVE_LOCAL_LIMIT, 'modern_native.py; transaction_limits.hh')
    check('native_node_ids', nodes['remote']+nodes['local'], 65536, 'modern_native.py')
    check('physical_windows', 3*ranks, 16, 'scaleout_transactions.py; transaction_session_validation.hh')
    check('barrier_wait_terms', ranks if nodes['local_events'].get('barrier_ready') else 0, 8,
          'scaleout_transactions.py; remote_memory.hh')
    check('registrations_minimum', bound, 32, 'memory_registrations.py', 'lower_bound')
    check('maximum_initialized_segments_per_rank', max(segments), 32, 'modern_native_layout.py; scaleout_transactions.py')
    check('maximum_initialized_bytes_per_rank', max(initial), 65536, 'modern_native_layout.py; scaleout_transactions.py')
    check('maximum_immutable_window_bytes', max(offsets), 64*2**20, 'modern_native_layout.py')
    return dict(schema_version='moe-modern-native-capacity/1',
                passed=all(row['passed'] for row in checks),
                scope='Static complete-workload requirements only; not native admission, execution, GPU/GIN timing or a latency prediction',
                network_executed=False, runtime_admitted=False, hardware_calibrated=False,
                counts=dict(logical_ranks=ranks, calls=len(config['calls']), modern_operations=len(plan['operations']),
                            modern_events=len(plan['events']), native_nodes=nodes,
                            prospective_connections=len(keys), self_connections=sum(a == b for a, _, b in keys),
                            remote_connections=sum(a != b for a, _, b in keys),
                            immutable_operands=[dict(rank=r, logical_window_bytes=offsets[r],
                                initialized_segments=segments[r], initialized_bytes=initial[r]) for r in range(ranks)],
                            logical_device_ids_used={str(r):sorted(d) for r,d in devices.items()}),
                checks=checks, failures=[row['name'] for row in checks if not row['passed']],
                unverified=['physical endpoint/NIC/plane mapping and actual registration count',
                            'main/signal window size, address overlap and memory permissions',
                            'dynamic proof frontier (maximum 32 dependencies)',
                            'physical control interactions (256), logical callback batches (16384), callback envelope bounds',
                            'packet/queue/recovery/host logs, result size and simulated cutoff',
                            'host wall time, RSS and address-space budget',
                            'binary/source/library identity and all runtime memory/buffer checks'])


def execute(config, output):
    config, output = Path(config).resolve(), Path(output).resolve()
    require(not output.exists(), 'capacity output already exists')
    value = checked_json(config, 1048576)
    report = requirements(value)
    report['input_sha256'] = digest(config)
    report['source_hashes'] = {p.name:digest(p) for p in (
        Path(__file__), Path(__file__).with_name('modern_native_layout.py'),
        Path(__file__).with_name('modern_native.py'), Path(__file__).with_name('modern_deepep_plan.py'))}
    # Exclusive creation is important even when another process creates it
    # between the initial check and the finished (potentially expensive) audit.
    output.parent.mkdir(parents=True, exist_ok=True)
    import json
    with output.open('x') as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    return report
