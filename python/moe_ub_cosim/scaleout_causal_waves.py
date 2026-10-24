"""Lower saved public frontend waves to causal persistent byte/local work.

This executes a declared reference DAG, not ASTRA scheduling or modern GIN.
RECV aliases real receive progress; it never sends or takes a compute slot.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import heapq
import json
from pathlib import Path

from .scaleout_rdma import checked_json, save
from .scaleout_streams import stream_limits, validate
from .scaleout_topology import fields, integer, require


def _refs(values):
    return [dict(work=i, event=e) for i, e in sorted(set(values))]


def compile_waves(waves, options, topology, protocol):
    """Pure compiler; file provenance is attached by the public entry point."""
    fields(options, 'schema_version waves streams limits'+''.join(' '+k for k in
           ('transport_recovery', 'dcqcn_reference', 'source_rate_capture') if k in options), 'causal waves')
    require(options['schema_version'] == 'moe-rdma-causal-waves/1', 'wrong causal waves schema')
    require(isinstance(waves, list) and 1 <= len(waves) <= 512 and
            isinstance(options['waves'], list) and len(options['waves']) == len(waves), 'wave count budget')
    program = dict(schema_version='moe-rdma-stream-work/4', limits=deepcopy(options['limits']),
                   local_model=dict(mode='serial_per_gpu/1', duration_source='declared_ns'),
                   streams=deepcopy(options['streams']), work=[])
    for k in ('transport_recovery', 'dcqcn_reference', 'source_rate_capture'):
        if k in options:
            program[k] = deepcopy(options[k])
    stream_limits(program)
    require(isinstance(program['streams'], list) and 1 <= len(program['streams']) <= 4096, 'stream count budget')
    bindings = {}
    for s in program['streams']:
        fields(s, 'id src dst plane wire_port source_nic destination_nic queue_depth', 'stream')
        for key in ('src', 'dst', 'plane', 'id'):
            integer(s[key], key, 0, 65535)
        key = s['src'], s['dst'], s['plane']
        require(key not in bindings, 'ambiguous stream binding')
        bindings[key] = s['id']

    groups, by_step, graph_ids, global_messages = {}, {}, set(), set()
    tasks, task_deps, senders, receivers, used_bindings = {}, {}, {}, {}, set()
    wave_nodes = defaultdict(list)
    for w in waves:
        fields(w, 'schema_version graph_id rank_ids group_index decode_step messages rank_nodes route_source routes', 'wave')
        require(w['schema_version'] == 'moe-scaleout-wave/1', 'wrong public wave schema')
        require(isinstance(w['graph_id'], str) and 0 < len(w['graph_id']) <= 128 and
                w['graph_id'] not in graph_ids, 'invalid/reused graph id')
        graph_ids.add(w['graph_id'])
        gi, step = w['group_index'], w['decode_step']
        integer(gi, 'group index', 0, 511); integer(step, 'decode step', 0, 127)
        ranks = w['rank_ids']
        require(isinstance(ranks, list) and 2 <= len(ranks) <= 32, 'require EP2..32')
        for r in ranks:
            integer(r, 'rank', 0, 1023)
        require(ranks == sorted(set(ranks)), 'ranks must be unique and sorted')
        require(gi not in groups or groups[gi] == ranks, 'group membership changed')
        groups[gi] = ranks
        require((gi, step) not in by_step, 'duplicate group step')
        by_step[gi, step] = w
        require(isinstance(w['rank_nodes'], dict) and set(map(str, ranks)) == set(w['rank_nodes']), 'rank node coverage')
        require(isinstance(w['messages'], list), 'invalid message array')
        messages = {}
        for m in w['messages']:
            fields(m, 'id tag src dst bytes plane layer phase contributions', 'wave message')
            integer(m['id'], 'message id', 0, 2**31-1)
            integer(m['tag'], 'message tag', 0, 2**31-1)
            require(m['id'] not in global_messages and m['id'] == m['tag'], 'reused/mismatched message identity')
            global_messages.add(m['id']); messages[m['id']] = m
            for k in ('src', 'dst'):
                integer(m[k], k, 0, 1023)
            require(m['src'] in ranks and m['dst'] in ranks and m['src'] != m['dst'], 'message leaves group or is self')
            integer(m['bytes'], 'message bytes', 1, 2**20); integer(m['plane'], 'message plane', 0, 15)
            integer(m['layer'], 'layer', 0, 127); integer(m['contributions'], 'contributions', 1, 2**31-1)
            require(m['phase'] in ('dispatch', 'combine'), 'unknown message phase')
            binding = m['src'], m['dst'], m['plane']
            require(binding in bindings, 'missing explicit stream binding')
            used_bindings.add(binding)
        for rank in ranks:
            rows = w['rank_nodes'][str(rank)]
            require(isinstance(rows, list) and rows, 'empty/invalid rank graph')
            for t in rows:
                require(isinstance(t, dict) and t.get('kind') in ('COMP', 'SEND', 'RECV'), 'unsupported frontend node')
                fields(t, 'id kind name deps duration_ns'+(' message' if t['kind'] != 'COMP' else ''), 'frontend node')
                integer(t['id'], 'node id', 0, 2**63-1)
                integer(t['duration_ns'], 'duration', 0, 5000000)
                require(isinstance(t['name'], str) and 0 < len(t['name']) <= 256, 'invalid node name')
                key = gi, step, rank, t['id']
                require(key not in tasks, 'duplicate node id')
                require(isinstance(t['deps'], list) and len(t['deps']) <= 64, 'invalid node dependencies')
                for d in t['deps']:
                    integer(d, 'node dependency', 0, 2**63-1)
                require(len(set(t['deps'])) == len(t['deps']), 'duplicate node dependency')
                tasks[key] = t; wave_nodes[gi, step].append(key)
                task_deps[key] = {(gi, step, rank, d) for d in t['deps']}
                if t['kind'] != 'COMP':
                    m = t['message']
                    require(isinstance(m, dict) and m.get('id') in messages and m == messages[m['id']], 'node/message mismatch')
                    require(t['duration_ns'] == 0 and rank == m['src' if t['kind'] == 'SEND' else 'dst'], 'invalid message endpoint/duration')
                    target = senders if t['kind'] == 'SEND' else receivers
                    require(m['id'] not in target, 'duplicate SEND or RECV')
                    target[m['id']] = key
        require(len(tasks) <= 65536, 'frontend node budget')
    require(set(senders) == set(receivers) == global_messages and global_messages, 'SEND/RECV message coverage')
    require(set(bindings) == used_bindings, 'unused explicit stream binding')
    flat = [r for ranks in groups.values() for r in ranks]
    require(len(flat) == len(set(flat)), 'groups overlap')
    for gi in groups:
        steps = sorted(step for group, step in by_step if group == gi)
        require(steps == list(range(len(steps))), 'group steps must start at zero and be contiguous')
    # A RECV is a conjunction: the posted local dependency AND actual bytes.
    # This global edge is needed even if the file lists RECV before SEND.
    for mid in global_messages:
        task_deps[receivers[mid]].add(senders[mid])
    for key, deps in task_deps.items():
        require(all(d in tasks for d in deps), 'unknown predecessor')

    work, completion, mapping, group_done, retirement = program['work'], {}, [], {}, []
    def add(row):
        require(len(work) < program['limits']['max_work'] and len(work) < 65536, 'compiled work count budget')
        row['id'] = len(work); work.append(row)
        return row['id']
    # Compile each whole wave after its previous group wave. Different groups
    # have no dependency on one another; list order is not simulated time.
    for gi, step in sorted(by_step, key=lambda x: (x[1], x[0])):
        w = by_step[gi, step]; keys = set(wave_nodes[gi, step])
        remaining = {k: len(task_deps[k]) for k in keys}
        successors = defaultdict(list)
        for k in keys:
            for d in task_deps[k]:
                successors[d].append(k)
        ready = [k for k in keys if not remaining[k]]; heapq.heapify(ready)
        previous = group_done.get((gi, step-1), [])
        visited = 0
        while ready:
            key = heapq.heappop(ready); t = tasks[key]; visited += 1
            local_parents = [(gi, step, key[2], d) for d in t['deps']]
            before = [ref for d in local_parents for ref in completion[d]] if local_parents else list(previous)
            if t['kind'] == 'RECV':
                sender = senders[t['message']['id']]
                sid, _ = completion[sender][0]
                refs = sorted(set([*before, (sid, 'accepted')]))
            else:
                require(len(set(before)) <= 64, 'expanded dependency budget')
                row = dict(kind='local', rank=key[2], duration_ns=t['duration_ns'], after=_refs(before))
                if t['kind'] == 'SEND':
                    m = t['message']; binding = m['src'], m['dst'], m['plane']
                    row = dict(kind='write', stream=bindings[binding], bytes=m['bytes'], after=_refs(before))
                wid = add(row); refs = [(wid, 'completed' if t['kind'] == 'COMP' else 'acked')]
            completion[key] = refs
            mapping.append(dict(graph_id=w['graph_id'], group_index=gi, decode_step=step, rank=key[2],
                                node_id=t['id'], kind=t['kind'], completion=_refs(refs)))
            for child in successors[key]:
                remaining[child] -= 1
                if not remaining[child]:
                    heapq.heappush(ready, child)
        require(visited == len(keys), 'frontend/global receive dependency cycle')
        # All nodes count, including SENDs that no later COMP references. The
        # zero-time joins serve only group retirement, never phase barriers.
        refs = sorted({ref for k in keys for ref in completion[k]})
        joins = []
        while len(refs) > 1:
            following = []
            for start in range(0, len(refs), 64):
                wid = add(dict(kind='local', rank=groups[gi][0], duration_ns=0, after=_refs(refs[start:start+64])))
                joins.append(wid); following.append((wid, 'completed'))
            refs = following
        group_done[gi, step] = refs
        # A sole reference still must be retained as the next-wave gate.
        w_done = dict(graph_id=w['graph_id'], group_index=gi, decode_step=step,
                      completion=_refs(refs), join_work=joins)
        retirement.append(w_done)
    validate(topology, protocol, program)
    manifest = dict(schema_version='moe-rdma-causal-map/1', nodes=mapping, waves=retirement,
                    source_routes=[dict(graph_id=w['graph_id'], label=w['route_source']) for w in waves],
                    scope='saved frontend dependencies on persistent byte/local reference; no ASTRA scheduling equivalence',
                    modern_gin_connected=False, gpu_visibility_modeled=False, hardware_calibrated=False)
    return program, manifest


def execute(config, topology, protocol, output):
    config, topology, protocol, output = (Path(p).resolve() for p in (config, topology, protocol, output))
    options = checked_json(config, 2**20)
    require(isinstance(options, dict) and isinstance(options.get('waves'), list) and
            1 <= len(options['waves']) <= 512, 'invalid wave files')
    waves, sources, total = [], [], 0
    for name in options['waves']:
        require(isinstance(name, str) and name and '\x00' not in name, 'invalid wave path')
        p = (config.parent/name).resolve()
        total += p.stat().st_size
        require(total <= 16*2**20, 'total wave input byte budget')
        raw = p.read_bytes(); waves.append(json.loads(raw))
        sources.append(dict(path=name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    t, c = checked_json(topology/'topology.json', 8*2**20), checked_json(protocol, 65536)
    program, manifest = compile_waves(waves, options, t, c)
    manifest['inputs'] = sources
    manifest['config_sha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
    manifest['topology_sha256'] = hashlib.sha256((topology/'topology.json').read_bytes()).hexdigest()
    manifest['protocol_sha256'] = hashlib.sha256(protocol.read_bytes()).hexdigest()
    for item in (program, manifest):
        require(len((json.dumps(item, indent=2)+'\n').encode()) <= 8*2**20, 'compiled JSON byte budget')
    output.mkdir(exist_ok=False)
    save(output/'work.json', program); save(output/'mapping.json', manifest)
    return dict(passed=True, output=str(output), waves=len(waves), streams=len(program['streams']),
                work=len(program['work']), frontend_nodes=len(manifest['nodes']),
                state='causal_persistent_reference_not_modern_GIN_or_ASTRA_equivalence')
