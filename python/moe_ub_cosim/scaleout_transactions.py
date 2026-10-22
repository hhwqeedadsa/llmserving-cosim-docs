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


def validate(topology, protocol, config):
    fields(config, 'schema_version connections windows operations', 'transaction root')
    require(config['schema_version'] == 'moe-rdma-transactions/1', 'unsupported transaction schema')
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
    validate_streams(topology, protocol, dict(schema_version='moe-rdma-stream-work/1', streams=streams,
        work=[dict(id=0, stream=streams[0]['id'], bytes=1, after=[])]))
    windows = config['windows']
    require(isinstance(windows, list) and 1 <= len(windows) <= 16, 'require 1..16 remote windows')
    gpus = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    registered = {}
    for w in windows:
        fields(w, 'owner key address allow_atomic hex', 'remote window')
        integer(w['owner'], 'window owner', 0, 31)
        integer(w['key'], 'remote key', 1, 2**32-1)
        integer(w['address'], 'window address', 0, 2**64-1)
        require(w['owner'] in gpus and isinstance(w['allow_atomic'], bool), 'invalid window owner/permission')
        length = _hex(w['hex'], 65536, 'window')
        require(w['address']+length <= 2**64-1, 'window address wraps')
        key = w['owner'], w['key']
        require(key not in registered, 'repeated owner/remote key')
        for prior, n in registered.values():
            require(prior['owner'] != w['owner'] or w['address']+length <= prior['address'] or
                    prior['address']+n <= w['address'], 'overlapping remote windows')
        registered[key] = w, length
    operations = config['operations']
    require(isinstance(operations, list) and 1 <= len(operations) <= 32, 'require 1..32 operations')
    seen, ancestors, accesses = set(), {}, []
    for op in operations:
        require(isinstance(op, dict) and op.get('verb') in ('put', 'fetch_add'), 'unsupported transaction verb')
        fields(op, 'id connection verb key address after '+('hex' if op['verb'] == 'put' else 'value'), 'operation')
        integer(op['id'], 'operation id', 0, 65535)
        integer(op['connection'], 'operation connection', 0, 7)
        integer(op['key'], 'operation remote key', 1, 2**32-1)
        integer(op['address'], 'operation address', 0, 2**64-1)
        require(op['id'] not in seen and op['connection'] in by_id, 'repeated operation/unknown connection')
        key = by_id[op['connection']]['dst'], op['key']
        require(key in registered, 'unknown remote owner/key')
        window, size = registered[key]
        if op['verb'] == 'put':
            length = _hex(op['hex'], 32768, 'put')
        else:
            integer(op['value'], 'atomic addend', 0, 2**64-1)
            require(window['allow_atomic'] and op['address']%8 == 0, 'atomic denied/unaligned')
            length = 8
        require(window['address'] <= op['address'] and op['address']+length <= window['address']+size,
                'remote address out of bounds')
        require(isinstance(op['after'], list) and len(op['after']) <= 32, 'invalid operation dependencies')
        deps, parents = set(), set()
        for d in op['after']:
            fields(d, 'operation event', 'transaction dependency')
            integer(d['operation'], 'dependency operation', 0, 65535)
            require(d['operation'] in seen and d['event'] in ('applied', 'request_acked', 'completed'),
                    'dependency must reference an earlier operation event')
            item = d['operation'], d['event']
            require(item not in deps, 'repeated dependency'); deps.add(item)
            parents.update(ancestors[d['operation']]); parents.add(d['operation'])
        for prior, prior_key, n in accesses:
            if key == prior_key and op['address'] < prior['address']+n and prior['address'] < op['address']+length:
                require(op['verb'] == prior['verb'] == 'fetch_add' or prior['id'] in parents,
                        'overlapping non-atomic memory writes require explicit causal order')
        seen.add(op['id']); ancestors[op['id']] = parents; accesses.append((op, key, length))


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
    require(result['passed'] and result['native_network_executed'] and result['drained'] and
            transport['physical_drained'] and transport['closed'] and result['completed_operations'] == len(w['operations']),
            'remote memory transaction probe incomplete')
    return dict(passed=True, output=str(output), completed_operations=result['completed_operations'],
                state='functional_native_remote_memory_transactions_not_GIN_timing', hardware_calibrated=False)
