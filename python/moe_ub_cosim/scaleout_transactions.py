"""Functional remote memory requests/responses on native packets; not RoCE timing."""
import os
from pathlib import Path
import re

from .scaleout_rdma import checked_json, digest, limits, monitored, save, ROOT
from .native_identity import locked_native_commit
from .scaleout_streams import validate as validate_streams
from .scaleout_topology import fields, integer, require
from .memory_registrations import validate_registrations
from .transaction_capacity import execution_limits, capacity, host_budget
from .application_windows import validate_application_windows
from .static_stream_validation import StaticStreamValidationCache


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


def _local_waits(waits, owner, registered, maximum=8):
    require(isinstance(waits, list) and 1 <= len(waits) <= maximum, 'local wait condition budget exceeded')
    locations, ranges = set(), []
    for wait in waits:
        require(isinstance(wait, dict), 'local wait must be an object')
        fields(wait, 'key address compare value'+(' reset_to' if 'reset_to' in wait else '')+
               (' bits' if 'bits' in wait else ''), 'local wait')
        bits = wait.get('bits', 64)
        require(type(bits) is int and bits in (32, 64), 'local wait bits must be 32 or 64')
        integer(wait['key'], 'local key', 1, 2**32-1)
        integer(wait['address'], 'local address', 0, 2**64-1)
        integer(wait['value'], 'local wait value', 0, 2**bits-1)
        if 'reset_to' in wait: integer(wait['reset_to'], 'local consume value', 0, 2**bits-1)
        location = owner, wait['key']
        require(location in registered and wait['compare'] in ('eq', 'ge'), 'unknown local owner/key or comparison')
        window, size = registered[location]
        require(wait['address']%(bits//8) == 0 and window['address'] <= wait['address'] and
                wait['address']+bits//8 <= window['address']+size, 'local wait address out of bounds/unaligned')
        item = wait['key'], wait['address']
        require(item not in locations, 'repeated local wait location'); locations.add(item)
        require(all(wait['key'] != key or wait['address']+bits//8 <= start or end <= wait['address']
                    for key, start, end in ranges), 'overlapping local wait ranges')
        ranges.append((wait['key'], wait['address'], wait['address']+bits//8))


def validate(topology, protocol, config, *, allow_no_remote=False, stream_cache=None):
    require(stream_cache is None or type(stream_cache) is StaticStreamValidationCache,
            'invalid static streams validation cache')
    local_mode = isinstance(config, dict) and config.get('schema_version') in ('moe-rdma-transactions/2', 'moe-rdma-transactions/3', 'moe-rdma-transactions/4', 'moe-rdma-transactions/5')
    sparse_mode = isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/5'
    memory_mode = sparse_mode or (isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/4')
    source_mode = memory_mode or (isinstance(config, dict) and config.get('schema_version') == 'moe-rdma-transactions/3')
    explicit = isinstance(config, dict) and 'submission_model' in config
    peer_copy = isinstance(config, dict) and 'peer_copy_model' in config
    if peer_copy:
        require(sparse_mode and topology.get('schema_version') == 'moe-scaleout-physical-topology/2' and config['peer_copy_model'] == dict(mode='topology_intra_host_crossbar/1') and
                topology.get('intra_host', {}).get('enabled') is True and
                topology['intra_host'].get('kind') == 'nonblocking_chunked_crossbar' and
                'application_window_model' not in config and 'buffer_model' not in config,
                'peer copy requires v5 enabled topology crossbar without application views/bounded pool')
    counts = execution_limits(config)
    caps = capacity(config)
    reference = 'dcqcn_reference' in config
    selective = isinstance(config.get('transport_recovery'), dict) and config['transport_recovery'].get('mode') == 'selective_repeat/1'
    if reference or selective:
        require(sparse_mode and 'buffer_model' not in config, 'transaction reference/selective transport requires v5 without bounded copied payload pools')
    require(not selective or reference, 'transaction selective transport requires dcqcn_reference')
    fields(config, 'schema_version connections windows operations'+(' local_model' if local_mode else '')+
           (' source_model' if source_mode else '')+(' local_memory_model' if memory_mode else '')+(' submission_model' if explicit and sparse_mode else '')+
           (' transport_recovery' if 'transport_recovery' in config else '')+
           (' application_window_model' if 'application_window_model' in config else '')+(' application_windows' if 'application_windows' in config else '')+
           (' registration_model' if 'registration_model' in config else '')+(' registrations' if 'registrations' in config else '')+
           (' buffer_model' if 'buffer_model' in config else '')+
           (' execution_limits' if 'execution_limits' in config else '')+
           (' dcqcn_reference' if reference else '')+
           (' self_peer_model' if 'self_peer_model' in config else '')+
           (' peer_copy_model' if peer_copy else ''), 'transaction root')
    require(config['schema_version'] in ('moe-rdma-transactions/1', 'moe-rdma-transactions/2', 'moe-rdma-transactions/3', 'moe-rdma-transactions/4', 'moe-rdma-transactions/5'), 'unsupported transaction schema')
    if explicit:
        require(config['submission_model'] in (dict(mode='explicit_prefix/1'), dict(mode='staged_prefix/1')), 'unsupported submission model')
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
    require(isinstance(connections, list) and 1 <= len(connections) <= counts['max_connections'], 'connection count budget exceeded')
    by_id, streams = {}, []
    for c in connections:
        require(isinstance(c,dict),'connection must be an object')
        fields(c, 'id src dst plane source_nic destination_nic queue_depth '+
               ('self_service' if 'self_service' in c else 'request_port response_port'), 'connection')
        integer(c['id'], 'connection id', 0, counts['max_connections']-1)
        integer(c['src'], 'transaction source rank', 0, caps['owner'])
        integer(c['dst'], 'transaction destination rank', 0, caps['owner'])
        integer(c['queue_depth'], 'transaction depth', 1, 32)
        require(c['queue_depth']&(c['queue_depth']-1)==0,'queue depth must be power of two')
        require(c['id'] not in by_id, 'repeated connection id')
        by_id[c['id']] = c
        if 'self_service' in c:continue
        for reverse in (False, True):
            streams.append(dict(id=c['id']*2+int(reverse), src=c['dst'] if reverse else c['src'],
                dst=c['src'] if reverse else c['dst'], plane=c['plane'],
                wire_port=c['response_port'] if reverse else c['request_port'],
                source_nic=c['destination_nic'] if reverse else c['source_nic'],
                destination_nic=c['source_nic'] if reverse else c['destination_nic'], queue_depth=c['queue_depth']))
    # Validate real physical reachability and native protocol using descriptors
    # only. These placeholder one-byte works are NEVER sent or saved as workload.
    require(streams,'self-peer service currently requires at least one remote connection')
    descriptor=dict(schema_version='moe-rdma-stream-work/1', streams=streams,
        work=[dict(id=0, stream=streams[0]['id'], bytes=1, after=[])])
    if 'execution_limits' in config:
        descriptor.update(schema_version='moe-rdma-stream-work/2',
            limits=dict(max_streams=2*counts['max_connections'], max_work=2*counts['max_remote_operations']))
    if caps['scaled']:
        descriptor['schema_version'] = 'moe-rdma-stream-work/4'
        descriptor['work'][0]['kind'] = 'write'
        descriptor['limits']['max_link_transmissions'] = 2000000
        descriptor['local_model'] = dict(mode='serial_per_gpu/1', duration_source='declared_ns')
    if 'transport_recovery' in config:descriptor['transport_recovery']=config['transport_recovery']
    if reference:descriptor['dcqcn_reference']=config['dcqcn_reference']
    if stream_cache is None:
        validate_streams(topology, protocol, descriptor, transaction_peer_copies=peer_copy)
    else:
        stream_cache.validate(topology, protocol, descriptor, transaction_peer_copies=peer_copy)
    windows = config['windows']
    require(isinstance(windows, list) and 1 <= len(windows) <= caps['windows'], 'remote window budget exceeded')
    gpus = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    registered = {}
    for w in windows:
        fields(w, 'owner key address allow_atomic '+('bytes initialization segments' if sparse_mode else 'hex'), 'remote window')
        integer(w['owner'], 'window owner', 0, caps['owner'])
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
    registrations=validate_registrations(config,registered,by_id)
    operations = config['operations']
    require(isinstance(operations, list) and (0 if allow_no_remote else 1) <= len(operations) <=
            (counts['max_remote_operations']+counts['max_local_operations'] if local_mode else 32), 'transaction node budget exceeded')
    kinds, ancestors, accesses = {}, {}, []
    remote_count = local_count = 0
    before_effects, effects, remote_history = {}, {}, []
    for op in operations:
        require(isinstance(op, dict), 'transaction node must be an object')
        kind = op.get('kind') if local_mode else 'remote'
        require(kind in ('remote', 'local'), 'unsupported transaction node kind')
        integer(op.get('id'), 'operation id', 0, 65535)
        require(op['id'] not in kinds, 'repeated operation id')
        require(isinstance(op.get('after'), list) and len(op['after']) <= caps['dependencies'], 'invalid operation dependencies')
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
                   (' copy' if 'copy' in op and memory_mode else '')+(' application_uses' if 'application_uses' in op else '')+
                   (' release_window' if 'release_window' in op else ''), 'local transaction node')
            integer(op['rank'], 'local rank', 0, caps['owner'])
            require(op['rank'] in gpus, 'local rank is not a topology GPU')
            integer(op['duration_ns'], 'local duration', 0, protocol.get('limits', {}).get('max_sim_ns', 5000000))
            if 'local_wait' in op: _local_waits(op['local_wait'], op['rank'], registered, caps['waits'])
            if 'copy' in op:
                copy = op['copy']; fields(copy, 'source target', 'local copy')
                source, target = copy['source'], copy['target']
                is_peer = 'owner' in target
                fields(source, 'key address bytes', 'local copy source')
                fields(target, 'key address'+(' owner' if is_peer else ''), 'local copy target')
                target_owner = target['owner'] if is_peer else op['rank']
                if is_peer:
                    integer(target_owner, 'peer copy target owner', 0, caps['owner'])
                    nodes = {n['id']: n for n in topology['nodes']}
                    require(peer_copy and target_owner in gpus and target_owner != op['rank'] and
                            nodes[target_owner]['server'] == nodes[op['rank']]['server'] and op['duration_ns'] == 0,
                            'peer copy requires same-server different GPU and zero fixed duration')
                integer(source['bytes'], 'local copy bytes', 1, 32768)
                for item, owner in ((source, op['rank']), (target, target_owner)):
                    integer(item['key'], 'local copy key', 1, 2**32-1)
                    integer(item['address'], 'local copy address', 0, 2**64-1)
                    key = owner, item['key']
                    require(key in registered, 'unknown local copy owner/key')
                    window, size = registered[key]
                    require(window['address'] <= item['address'] and item['address']+source['bytes'] <= window['address']+size,
                            'local copy range out of bounds')
                    require(all(owner != op['rank'] or 'reset_to' not in w or w['address']+w.get('bits',64)//8 <= item['address'] or
                                item['address']+source['bytes'] <= w['address'] for w in op.get('local_wait', [])),
                            'local copy overlaps own condition reset')
                require(target_owner != op['rank'] or source['address']+source['bytes'] <= target['address'] or
                        target['address']+source['bytes'] <= source['address'], 'local copy source/target overlap')
            local_count += 1
            require(local_count <= counts['max_local_operations'], 'local transaction node budget exceeded')
            kinds[op['id']] = kind; ancestors[op['id']] = parents
            before_effects[op['id']] = safe_parents
            continue
        remote_count += 1
        require(remote_count <= counts['max_remote_operations'], 'remote transaction budget exceeded')
        require(isinstance(op, dict) and op.get('verb') in ('put', 'fetch_add', 'fetch_add32'), 'unsupported transaction verb')
        from_source = 'source' in op
        require(not from_source or (source_mode and op['verb'] == 'put' and 'hex' not in op), 'source requires v3 put without hex')
        fields(op, 'id connection verb key address after '+('source' if from_source else 'hex' if op['verb'] == 'put' else 'value')+
               (' kind' if local_mode else '')+(' aggregate_requests' if explicit else '')+
               (' local_wait' if 'local_wait' in op else '')+(' queue_fence' if 'queue_fence' in op else '')+
               (' application_uses' if 'application_uses' in op else '')+(' release_window' if 'release_window' in op else ''), 'operation')
        if explicit: require(type(op['aggregate_requests']) is bool, 'aggregate_requests must be boolean')
        integer(op['connection'], 'operation connection', 0, counts['max_connections']-1)
        integer(op['key'], 'operation remote key', 1, 2**32-1)
        integer(op['address'], 'operation address', 0, 2**64-1)
        require(op['connection'] in by_id, 'unknown connection')
        key = by_id[op['connection']]['dst'], op['key']
        access=None
        if registrations is not None:
            location=key[0],by_id[op['connection']]['destination_nic'],key[1]
            require(location in registrations,'unknown destination NIC/key registration')
            access=registrations[location];key=key[0],access['window_key']
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
            length = 4 if op['verb'] == 'fetch_add32' else 8
            integer(op['value'], 'atomic addend', 0, 2**(length*8)-1)
            require(window['allow_atomic'] and (access is None or access['allow_atomic']) and op['address']%length == 0, 'atomic denied/unaligned')
        require(window['address'] <= op['address'] and op['address']+length <= window['address']+size,
                'remote address out of bounds')
        if access is not None:
            require(access['address']<=op['address'] and op['address']+length<=access['address']+access['bytes'],
                    'remote registration range exceeded')
        if 'queue_fence' in op:
            selected = op['queue_fence']
            require('local_wait' not in op, 'queue fence and local wait require separate application steps')
            require(isinstance(selected, list) and 1 <= len(selected) <= counts['max_connections'], 'queue fence count budget exceeded')
            for connection in selected:
                integer(connection, 'queue fence connection', 0, counts['max_connections']-1)
                require(connection in by_id and by_id[connection]['src'] == by_id[op['connection']]['src'],
                        'queue fence must select source-owned connections')
            require(len(set(selected)) == len(selected), 'repeated queue fence connection')
        if 'local_wait' in op:
            _local_waits(op['local_wait'], by_id[op['connection']]['src'], registered, caps['waits'])
            if from_source:
                require(all('reset_to' not in w or w['address']+w.get('bits',64)//8 <= source['address'] or
                            source['address']+length <= w['address'] for w in op['local_wait']),
                        'source overlaps own condition reset; use separate steps')
        for prior, prior_key, n in accesses:
            if key == prior_key and op['address'] < prior['address']+n and prior['address'] < op['address']+length:
                require((op['verb'] in ('fetch_add','fetch_add32') and prior['verb'] in ('fetch_add','fetch_add32')) or prior['id'] in safe_parents or
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
    if peer_copy:
        require(sum(op.get('kind') == 'local' and 'owner' in op.get('copy', {}).get('target', {})
                    for op in operations) <= limits(protocol)['max_messages'], 'peer flow count exceeds protocol budget')
    from .self_peer_service import validate as validate_self
    validate_self(topology,protocol,config)
    validate_application_windows(config, registered, by_id)
    require(allow_no_remote or remote_count > 0, 'transaction probe requires remote work')
    if 'buffer_model' in config:
        _bounded_buffers(config, registered, by_id)


def _bounded_buffers(config, registered, connections):
    model = config['buffer_model']
    fields(model, 'mode transport_bytes_per_source registered_bytes_per_owner target_cache_bytes_per_owner local_copy_bytes_per_owner'+
           (' on_source_isolation' if isinstance(model, dict) and 'on_source_isolation' in model else '')+
           (' target_delivery' if isinstance(model, dict) and 'target_delivery' in model else ''), 'buffer model')
    once = 'target_delivery' in model
    if once:
        require(model['target_delivery'] == 'carrier_once_put/1', 'unsupported target delivery mode')
    if 'on_source_isolation' in model:
        require(model['on_source_isolation'] == 'release_copied_payload/1' and
                config.get('transport_recovery', {}).get('on_exhausted') == 'isolate_stream',
                'copied payload release requires release_copied_payload/1 and explicit source isolation')
    require(model['mode'] == 'bounded_copied_put/1' and config['schema_version'] == 'moe-rdma-transactions/4'
            and 'submission_model' not in config, 'bounded copied buffers require transactions/4 without explicit submission')
    for key, low, high in (('transport_bytes_per_source', 33, 2*1024*1024),
                           ('registered_bytes_per_owner', 1, 1024*1024),
                           ('target_cache_bytes_per_owner', 0 if once else 33, 0 if once else 2*1024*1024),
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
        require(once or cached[owner] <= model['target_cache_bytes_per_owner'], 'retained target request cache capacity exceeded')


def probe(topology, protocol, config, backend, output):
    topology, protocol, config, backend, output = (Path(p).resolve() for p in (topology, protocol, config, backend, output))
    t, c, w = (checked_json(p, n) for p, n in ((topology/'topology.json', 8388608), (protocol, 65536), (config, 4*2**20)))
    validate(t, c, w)
    require(w.get('submission_model') != dict(mode='staged_prefix/1'),
            'staged_prefix/1 requires a controller using NativeTransactionSession')
    require(backend.is_file() and os.access(backend, os.X_OK), 'pass the native transaction-probe executable')
    budget = host_budget(w, limits(c))
    native_commit = locked_native_commit(ROOT)
    output.mkdir(exist_ok=False)
    command = [backend, topology/'topology.json', protocol, config, output/'result.json']
    save(output/'inputs.json', dict(command=list(map(str, command)), native_commit=native_commit,
        native_commit_role='expected_source_checkout_not_binary_build_identity',
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
