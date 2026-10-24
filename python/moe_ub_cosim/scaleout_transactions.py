"""Functional remote memory requests/responses on native packets; not RoCE timing."""
import os
from pathlib import Path
import re

from .scaleout_rdma import checked_json, digest, limits, monitored, save, NATIVE_COMMIT
from .scaleout_streams import validate as validate_streams
from .scaleout_topology import fields, integer, require


def _hex(text, maximum, name):
    require(isinstance(text, str) and 2 <= len(text) <= maximum*2 and len(text)%2 == 0 and
            re.fullmatch('[0-9a-f]+', text) is not None, name+' requires bounded lowercase hexadecimal bytes')
    return len(text)//2


def sparse_window_size(window):
    """Validate declared zero storage; never allocate the logical window."""
    integer(window['bytes'], 'sparse window bytes', 1, 64*2**20)
    require(window['initialization'] == 'zero_fill/1', 'unsupported sparse initialization')
    segments = window['segments']
    require(isinstance(segments, list) and len(segments) <= 32, 'sparse initialization segment budget exceeded')
    ranges, total = [], 0
    for segment in segments:
        fields(segment, 'offset hex', 'sparse initialization segment')
        integer(segment['offset'], 'sparse initialization offset', 0, window['bytes']-1)
        size = _hex(segment['hex'], 65536, 'sparse initialization')
        start, end = segment['offset'], segment['offset']+size
        total += size
        require(end <= window['bytes'] and total <= 65536, 'sparse initialization range/byte budget exceeded')
        require(all(end <= a or b <= start for a, b in ranges), 'overlapping sparse initialization')
        ranges.append((start, end))
    return window['bytes']


def _local_waits(waits, owner, registered):
    require(isinstance(waits, list) and 1 <= len(waits) <= 8, 'require 1..8 local wait conditions')
    locations = set()
    for wait in waits:
        require(isinstance(wait, dict), 'local wait must be an object')
        fields(wait, 'key address compare value'+(' reset_to' if 'reset_to' in wait else ''), 'local wait')
        integer(wait['key'], 'local key', 1, 2**32-1)
        integer(wait['address'], 'local address', 0, 2**64-1)
        integer(wait['value'], 'local wait value', 0, 2**64-1)
        if 'reset_to' in wait: integer(wait['reset_to'], 'local consume value', 0, 2**64-1)
        location = owner, wait['key']
        require(location in registered and wait['compare'] in ('eq', 'ge'), 'unknown local owner/key or comparison')
        window, size = registered[location]
        require(wait['address']%8 == 0 and window['address'] <= wait['address'] and
                wait['address']+8 <= window['address']+size, 'local wait address out of bounds/unaligned')
        item = wait['key'], wait['address']
        require(item not in locations, 'repeated local wait location'); locations.add(item)


def validate(topology, protocol, config, *, allow_no_remote=False):
    local_mode = isinstance(config, dict) and config.get('schema_version') in ('moe-rdma-transactions/2', 'moe-rdma-transactions/3', 'moe-rdma-transactions/4', 'moe-rdma-transactions/5')
    sparse_mode = isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/5'
    memory_mode = sparse_mode or (isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/4')
    source_mode = memory_mode or (isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/3')
    explicit = isinstance(config, dict) and 'submission_model' in config
    fields(config, 'schema_version connections windows operations'+(' local_model' if local_mode else '')+
           (' source_model' if source_mode else '')+(' local_memory_model' if memory_mode else '')+(' submission_model' if explicit and sparse_mode else '')+
           (' transport_recovery' if 'transport_recovery' in config else '')+
           (' buffer_model' if 'buffer_model' in config else ''), 'transaction root')
    require(config['schema_version'] in ('moe-rdma-transactions/1', 'moe-rdma-transactions/2', 'moe-rdma-transactions/3', 'moe-rdma-transactions/4', 'moe-rdma-transactions/5'), 'unsupported transaction schema')
    if explicit:
        require(config['submission_model'] == dict(mode='explicit_prefix/1'), 'unsupported submission model')
    if local_mode:
        require(config['local_model'] == dict(mode='serial_per_gpu/1', duration_source='declared_ns'),
                'unsupported transaction local model')
    if source_mode:
        require(config['source_model'] == dict(capture='registered_window_at_publish/1', release='transaction_completed/1'),
                'unsupported transaction source model')
    if memory_mode:
        require(config['local_memory_model'] == dict(mode='guarded_copy/1', acquire='admission/1', capture='start/1', commit='completion/1'),
                'unsupported local memory model')
    connections = config['connections']
    require(isinstance(connections, list) and 1 <= len(connections) <= 8, 'require 1..8 connections')
    by_id, streams = {}, []
    for c in connections:
        fields(c, 'id src dst plane request_port response_port source_nic destination_nic queue_depth', 'connection')
        integer(c['id'], 'connection id', 0, 7)
        integer(c['queue_depth'], 'transaction depth', 1, 32)
        require(c['id'] not in by_id, 'repeated connection id')
        by_id[c['id']] = c
        for reverse in (False, True):
            streams.append(dict(id=c['id']*2+int(reverse), src=c['dst'] if reverse else c['src'],
                dst=c['src'] if reverse else c['dst'], plane=c['plane'],
                wire_port=c['response_port'] if reverse else c['request_port'],
                source_nic=c['destination_nic'] if reverse else c['source_nic'],
                destination_nic=c['source_nic'] if reverse else c['destination_nic'], queue_depth=c['queue_depth']))
    # Validate real physical reachability and native protocol using descriptors
    # only. These placeholder one-byte works are NEVER sent or saved as workload.
    descriptor=dict(schema_version='moe-rdma-stream-work/1', streams=streams,
        work=[dict(id=0, stream=streams[0]['id'], bytes=1, after=[])])
    if 'transport_recovery' in config:descriptor['transport_recovery']=config['transport_recovery']
    validate_streams(topology, protocol, descriptor)
    windows = config['windows']
    require(isinstance(windows, list) and 1 <= len(windows) <= 16, 'require 1..16 remote windows')
    gpus = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    registered = {}
    for w in windows:
        fields(w, 'owner key address allow_atomic '+('bytes initialization segments' if sparse_mode else 'hex'), 'remote window')
        integer(w['owner'], 'window owner', 0, 31)
        integer(w['key'], 'remote key', 1, 2**32-1)
        integer(w['address'], 'window address', 0, 2**64-1)
        require(w['owner'] in gpus and isinstance(w['allow_atomic'], bool), 'invalid window owner/permission')
        length = sparse_window_size(w) if sparse_mode else _hex(w['hex'], 65536, 'window')
        require(w['address']+length <= 2**64-1, 'window address wraps')
        key = w['owner'], w['key']
        require(key not in registered, 'repeated owner/remote key')
        for prior, n in registered.values():
            require(prior['owner'] != w['owner'] or w['address']+length <= prior['address'] or
                    prior['address']+n <= w['address'], 'overlapping remote windows')
        registered[key] = w, length
    operations = config['operations']
    require(isinstance(operations, list) and (0 if allow_no_remote else 1) <= len(operations) <= (48 if local_mode else 32), 'transaction node budget exceeded')
    kinds, ancestors, accesses = {}, {}, []
    remote_count = local_count = 0
    before_effects, effects, remote_history = {}, {}, []
    for op in operations:
        require(isinstance(op, dict), 'transaction node must be an object')
        kind = op.get('kind') if local_mode else 'remote'
        require(kind in ('remote', 'local'), 'unsupported transaction node kind')
        integer(op.get('id'), 'operation id', 0, 65535)
        require(op['id'] not in kinds, 'repeated operation id')
        require(isinstance(op.get('after'), list) and len(op['after']) <= 32, 'invalid operation dependencies')
        deps, parents, safe_parents = set(), set(), set()
        for d in op['after']:
            fields(d, 'operation event', 'transaction dependency')
            integer(d['operation'], 'dependency operation', 0, 65535)
            require(d['operation'] in kinds and d['event'] in (
                ('completed',) if kinds[d['operation']] == 'local' else (('prepared',) if explicit else ())+('applied', 'request_acked', 'completed')),
                'dependency must reference an earlier matching node event')
            item = d['operation'], d['event']
            require(item not in deps, 'repeated dependency'); deps.add(item)
            parents.update(ancestors[d['operation']]); parents.add(d['operation'])
            safe_parents.update(before_effects[d['operation']])
            if kinds[d['operation']] == 'remote' and d['event'] != 'prepared':
                safe_parents.update(effects[d['operation']])
        if kind == 'local':
            fields(op, 'id kind rank duration_ns after'+(' local_wait' if 'local_wait' in op else '')+
                   (' copy' if 'copy' in op and memory_mode else ''), 'local transaction node')
            integer(op['rank'], 'local rank', 0, 31)
            require(op['rank'] in gpus, 'local rank is not a topology GPU')
            integer(op['duration_ns'], 'local duration', 0, protocol.get('limits', {}).get('max_sim_ns', 5000000))
            if 'local_wait' in op: _local_waits(op['local_wait'], op['rank'], registered)
            if 'copy' in op:
                copy = op['copy']; fields(copy, 'source target', 'local copy')
                source, target = copy['source'], copy['target']
                fields(source, 'key address bytes', 'local copy source'); fields(target, 'key address', 'local copy target')
                integer(source['bytes'], 'local copy bytes', 1, 32768)
                for item in (source, target):
                    integer(item['key'], 'local copy key', 1, 2**32-1)
                    integer(item['address'], 'local copy address', 0, 2**64-1)
                    key = op['rank'], item['key']
                    require(key in registered, 'unknown local copy owner/key')
                    window, size = registered[key]
                    require(window['address'] <= item['address'] and item['address']+source['bytes'] <= window['address']+size,
                            'local copy range out of bounds')
                    require(all('reset_to' not in w or w['address']+8 <= item['address'] or
                                item['address']+source['bytes'] <= w['address'] for w in op.get('local_wait', [])),
                            'local copy overlaps own condition reset')
                require(source['address']+source['bytes'] <= target['address'] or
                        target['address']+source['bytes'] <= source['address'], 'local copy source/target overlap')
            local_count += 1
            require(local_count <= 16, 'local transaction node budget exceeded')
            kinds[op['id']] = kind; ancestors[op['id']] = parents
            before_effects[op['id']] = safe_parents
            continue
        remote_count += 1
        require(remote_count <= 32, 'remote transaction budget exceeded')
        require(isinstance(op, dict) and op.get('verb') in ('put', 'fetch_add'), 'unsupported transaction verb')
        from_source = 'source' in op
        require(not from_source or (source_mode and op['verb'] == 'put' and 'hex' not in op), 'source requires v3 put without hex')
        fields(op, 'id connection verb key address after '+('source' if from_source else 'hex' if op['verb'] == 'put' else 'value')+
               (' kind' if local_mode else '')+(' aggregate_requests' if explicit else '')+
               (' local_wait' if 'local_wait' in op else '')+(' queue_fence' if 'queue_fence' in op else ''), 'operation')
        if explicit: require(type(op['aggregate_requests']) is bool, 'aggregate_requests must be boolean')
        integer(op['connection'], 'operation connection', 0, 7)
        integer(op['key'], 'operation remote key', 1, 2**32-1)
        integer(op['address'], 'operation address', 0, 2**64-1)
        require(op['connection'] in by_id, 'unknown connection')
        key = by_id[op['connection']]['dst'], op['key']
        require(key in registered, 'unknown remote owner/key')
        window, size = registered[key]
        if from_source:
            source = op['source']
            fields(source, 'key address bytes', 'put source')
            integer(source['key'], 'source key', 1, 2**32-1)
            integer(source['address'], 'source address', 0, 2**64-1)
            integer(source['bytes'], 'source bytes', 1, 32768)
            source_key = by_id[op['connection']]['src'], source['key']
            require(source_key in registered, 'unknown source owner/key')
            source_window, source_size = registered[source_key]
            length = source['bytes']
            require(source_window['address'] <= source['address'] and
                    source['address']+length <= source_window['address']+source_size, 'source address out of bounds')
        elif op['verb'] == 'put':
            length = _hex(op['hex'], 32768, 'put')
        else:
            integer(op['value'], 'atomic addend', 0, 2**64-1)
            require(window['allow_atomic'] and op['address']%8 == 0, 'atomic denied/unaligned')
            length = 8
        require(window['address'] <= op['address'] and op['address']+length <= window['address']+size,
                'remote address out of bounds')
        if 'queue_fence' in op:
            selected = op['queue_fence']
            require('local_wait' not in op, 'queue fence and local wait require separate application steps')
            require(isinstance(selected, list) and 1 <= len(selected) <= 8, 'require 1..8 queue fence connections')
            for connection in selected:
                integer(connection, 'queue fence connection', 0, 7)
                require(connection in by_id and by_id[connection]['src'] == by_id[op['connection']]['src'],
                        'queue fence must select source-owned connections')
            require(len(set(selected)) == len(selected), 'repeated queue fence connection')
        if 'local_wait' in op:
            _local_waits(op['local_wait'], by_id[op['connection']]['src'], registered)
            if from_source:
                require(all('reset_to' not in w or w['address']+8 <= source['address'] or
                            source['address']+length <= w['address'] for w in op['local_wait']),
                        'source overlaps own condition reset; use separate steps')
        for prior, prior_key, n in accesses:
            if key == prior_key and op['address'] < prior['address']+n and prior['address'] < op['address']+length:
                require(op['verb'] == prior['verb'] == 'fetch_add' or prior['id'] in safe_parents or
                        (prior['connection'] == op['connection'] and prior['id'] in parents),
                        'overlapping non-atomic memory writes require remote effect order; '
                        'prepared does not order writes across connections')
        completed_effects = safe_parents | {op['id']}
        for prior in remote_history:
            if prior['connection'] == op['connection'] and prior['id'] in parents:
                completed_effects.update(effects[prior['id']])
        before_effects[op['id']] = safe_parents
        effects[op['id']] = completed_effects
        remote_history.append(op)
        kinds[op['id']] = kind; ancestors[op['id']] = parents; accesses.append((op, key, length))
    require(allow_no_remote or remote_count > 0, 'transaction probe requires remote work')
    if 'buffer_model' in config:
        _bounded_buffers(config, registered, by_id)


def _bounded_buffers(config, registered, connections):
    model = config['buffer_model']
    fields(model, 'mode transport_bytes_per_source registered_bytes_per_owner target_cache_bytes_per_owner local_copy_bytes_per_owner'+
           (' on_source_isolation' if isinstance(model, dict) and 'on_source_isolation' in model else ''), 'buffer model')
    if 'on_source_isolation' in model:
        require(model['on_source_isolation'] == 'release_copied_payload/1' and
                config.get('transport_recovery', {}).get('on_exhausted') == 'isolate_stream',
                'copied payload release requires release_copied_payload/1 and explicit source isolation')
    require(model['mode'] == 'bounded_copied_put/1' and config['schema_version'] == 'moe-rdma-transactions/4'
            and 'submission_model' not in config, 'bounded copied buffers require transactions/4 without explicit submission')
    for key, low, high in (('transport_bytes_per_source', 33, 2*1024*1024),
                           ('registered_bytes_per_owner', 1, 1024*1024),
                           ('target_cache_bytes_per_owner', 33, 2*1024*1024),
                           ('local_copy_bytes_per_owner', 1, 32768)):
        integer(model[key], key, low, high)
    windows, cached = {}, {}
    for (owner, _), (_, size) in registered.items():
        windows[owner] = windows.get(owner, 0)+size
        require(windows[owner] <= model['registered_bytes_per_owner'], 'registered window capacity exceeded')
    for op in config['operations']:
        if op['kind'] == 'local':
            if 'copy' in op:
                require(op['copy']['source']['bytes'] <= model['local_copy_bytes_per_owner'], 'local copy snapshot capacity exceeded')
            continue
        require(op['verb'] == 'put' and 'source' in op and 'hex' not in op,
                'bounded copied buffers support source-window put only')
        size = op['source']['bytes']+32
        require(size <= model['transport_bytes_per_source'], 'one frame exceeds source transport capacity')
        owner = connections[op['connection']]['dst']
        cached[owner] = cached.get(owner, 0)+size
        require(cached[owner] <= model['target_cache_bytes_per_owner'], 'retained target request cache capacity exceeded')


def probe(topology, protocol, config, backend, output):
    topology, protocol, config, backend, output = (Path(p).resolve() for p in (topology, protocol, config, backend, output))
    t, c, w = (checked_json(p, n) for p, n in ((topology/'topology.json', 8388608), (protocol, 65536), (config, 4*2**20)))
    validate(t, c, w)
    require(backend.is_file() and os.access(backend, os.X_OK), 'pass the native transaction-probe executable')
    budget = limits(c)
    budget.update(wall_seconds=min(60, budget['wall_seconds']), rss_bytes=min(2**30, budget['rss_bytes']),
                  address_bytes=min(2*2**30, budget['address_bytes']), artifact_bytes=min(64*2**20, budget['artifact_bytes']))
    output.mkdir(exist_ok=False)
    command = [backend, topology/'topology.json', protocol, config, output/'result.json']
    save(output/'inputs.json', dict(command=list(map(str, command)), native_commit=NATIVE_COMMIT,
        scope='MRT1 functional transactions; not RoCE wire-equivalent, GIN or GPU visibility',
        hashes={str(p): digest(p) for p in (topology/'topology.json', protocol, config, backend)}))
    monitored(command, output/'execution', [output], runtime=budget)
    result = checked_json(output/'result.json', budget['result_json_bytes'])
    transport = result['transactions']['transport']
    remote_count = sum(op.get('kind') != 'local' for op in w['operations'])
    require(result['passed'] and result['native_network_executed'] and result['drained'] and
            transport['physical_drained'] and transport['closed'] and result['completed_operations'] == remote_count,
            'remote memory transaction probe incomplete')
    if w['schema_version'] in ('moe-rdma-transactions/2', 'moe-rdma-transactions/3', 'moe-rdma-transactions/4', 'moe-rdma-transactions/5'):
        require(result['completed_local_work']+remote_count == len(w['operations']), 'local transaction work incomplete')
    return dict(passed=True, output=str(output), completed_operations=result['completed_operations'],
                state='functional_native_remote_memory_transactions_not_GIN_timing', hardware_calibrated=False)
