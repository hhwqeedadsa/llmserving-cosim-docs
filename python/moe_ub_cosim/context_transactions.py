"""Explicit context identities bound to persistent MRT1/native transactions.

This plans connections, never substitutes for a GIN provider or GPU visibility.
Self-peer requests require a future provider and are rejected, never omitted.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .scaleout_rdma import checked_json, digest, save
from .scaleout_topology import fields, integer, require
from .scaleout_transactions import validate


def name(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', value) is not None,
            'invalid '+label)


def compile_program(topology, protocol, config):
    fields(config, 'schema_version communicators bindings program', 'context program')
    require(config['schema_version'] == 'moe-rdma-context-program/1', 'unsupported context program schema')
    gpus={n['id'] for n in topology['nodes'] if n['kind']=='gpu'}
    physical_ports={(edge[side],edge[side+'_port']) for edge in topology['links'] for side in ('src','dst')}
    comms, mapping, connections = {}, [], []
    require(isinstance(config['communicators'], list) and 1 <= len(config['communicators']) <= 8,
            'require 1..8 communicator declarations')
    for c in config['communicators']:
        fields(c, 'name requested_contexts connections ranks', 'communicator declaration')
        name(c['name'], 'communicator name');require(c['name'] not in comms, 'repeated communicator')
        integer(c['requested_contexts'], 'requested contexts', 1, 64)
        integer(c['connections'], 'GIN connections', 1, 4)
        require(isinstance(c['ranks'], list) and 2 <= len(c['ranks']) <= 32, 'require 2..32 communicator ranks')
        ranks, nodes = {}, set()
        for rank in c['ranks']:
            fields(rank, 'rank node nics', 'communicator rank')
            integer(rank['rank'], 'logical rank', 0, 31);integer(rank['node'], 'physical GPU node', 0, 31)
            require(rank['node'] in gpus, 'communicator node is not a physical GPU')
            require(rank['rank'] not in ranks and rank['node'] not in nodes, 'repeated logical rank or physical GPU')
            require(isinstance(rank['nics'], list) and 1 <= len(rank['nics']) <= 4, 'require 1..4 local NIC identifiers')
            for nic in rank['nics']:
                integer(nic, 'physical NIC port', 1, 1024)
                require((rank['node'],nic) in physical_ports, 'communicator NIC does not exist')
            ranks[rank['rank']] = rank;nodes.add(rank['node'])
        require(set(ranks) == set(range(len(ranks))), 'logical ranks must be contiguous from zero')
        comms[c['name']] = dict(declaration=c, ranks=ranks,
            allocated=(c['requested_contexts']+c['connections']-1)//c['connections']*c['connections'])
    bindings = config['bindings']
    require(isinstance(bindings, list) and 1 <= len(bindings) <= 64, 'require 1..64 bound connections')
    identities, by_name = set(), {}
    for binding in bindings:
        fields(binding, 'name communicator src_rank peer_rank context_index plane request_port response_port queue_depth', 'context binding')
        name(binding['name'], 'binding name');name(binding['communicator'], 'binding communicator')
        require(binding['name'] not in by_name and binding['communicator'] in comms, 'unknown communicator or repeated binding name')
        comm = comms[binding['communicator']];ranks = comm['ranks'];c = comm['declaration']
        integer(binding['src_rank'], 'source logical rank', 0, len(ranks)-1)
        integer(binding['peer_rank'], 'peer logical rank', 0, len(ranks)-1)
        require(binding['src_rank'] != binding['peer_rank'], 'self-peer provider transport is unsupported; do not omit required operations')
        integer(binding['context_index'], 'context index', 0, comm['allocated']-1)
        integer(binding['plane'], 'binding plane', 0, 15)
        for port in ('request_port', 'response_port'):integer(binding[port], port, 10000, 65535)
        integer(binding['queue_depth'], 'transaction queue depth', 1, 32)
        key = (binding['communicator'], binding['src_rank'], binding['context_index'], binding['peer_rank'])
        require(key not in identities, 'duplicate persistent context/peer identity; reuse its binding name')
        identities.add(key)
        source, peer = ranks[binding['src_rank']], ranks[binding['peer_rank']]
        connection = binding['context_index'] % c['connections']
        source_nic = source['nics'][connection % len(source['nics'])]
        destination_nic = peer['nics'][connection % len(peer['nics'])]
        identity = len(connections);by_name[binding['name']] = identity
        connections.append(dict(id=identity, src=source['node'], dst=peer['node'], plane=binding['plane'],
            request_port=binding['request_port'], response_port=binding['response_port'],
            source_nic=source_nic, destination_nic=destination_nic, queue_depth=binding['queue_depth']))
        mapping.append(dict(name=binding['name'], communicator=binding['communicator'],
            src_rank=binding['src_rank'], peer_rank=binding['peer_rank'], context_index=binding['context_index'],
            gin_connection=connection, local_context=binding['context_index']//c['connections'],
            allocated_contexts=comm['allocated'], transaction_connection=identity,
            request_stream=2*identity, response_stream=2*identity+1, **{k:v for k,v in connections[-1].items() if k!='id'}))
    program = deepcopy(config['program'])
    require(isinstance(program, dict) and program.get('schema_version') == 'moe-rdma-transactions/5' and
            'connections' not in program and isinstance(program.get('operations'), list),
            'require transactions/5 program without explicit connections')
    for op in program['operations']:
        require(isinstance(op, dict), 'transaction operation must be an object')
        if op.get('kind') == 'remote':
            require('connection' not in op, 'remote operation selects a binding, not a numeric connection')
            selected = op.pop('binding', None)
            require(isinstance(selected, str) and selected in by_name, 'unknown remote operation binding')
            op['connection'] = by_name[selected]
    program['connections'] = connections
    validate(topology, protocol, program)
    return program, dict(schema_version='moe-rdma-context-map/1', bindings=mapping,
        scope='explicit context identity -> MRT1 request/response carrier pair; not GIN QP or wire equivalence',
        self_peer_supported=False, gpu_visibility_modeled=False, hardware_calibrated=False)


def execute(config, topology, protocol, output):
    config, topology, protocol, output = (Path(p).resolve() for p in (config, topology, protocol, output))
    topology = topology/'topology.json'
    c,t,p = (checked_json(path, maximum) for path,maximum in ((config,4*2**20),(topology,8*2**20),(protocol,65536)))
    program, mapping = compile_program(t,p,c)
    require(not output.exists(), 'context plan output already exists')
    output.mkdir()
    for filename,value in [('input.json',c),('topology.json',t),('protocol.json',p),('transactions.json',program),('bindings.json',mapping)]:save(output/filename,value)
    save(output/'manifest.json',dict(schema_version='moe-rdma-context-plan/1',
        files={n:digest(output/n) for n in ('input.json','topology.json','protocol.json','transactions.json','bindings.json')},
        native_network_executed=False, scope=mapping['scope']))
    return dict(passed=True,output=str(output),connections=len(program['connections']),
        state='context_bound_MRT1_program_ready_not_GIN_execution')


def check_result(plan, result):
    """Actual source TX plus captured endpoint TX/RX; no timing/visibility inference."""
    cfg = plan['program'];mapping = plan['mapping'];connections = {c['id']:c for c in cfg['connections']}
    require(result['passed'] and result['native_network_executed'] and result['drained'], 'native transaction run incomplete')
    tx = result['transactions'];net = tx['transport']
    require(net['physical_drained'] and net['closed'] and net['inflight_packets']==0, 'physical transport not closed/drained')
    operations = {o['id']:o for o in tx['operations']};specs = {o['id']:o for o in cfg['operations'] if o['kind']=='remote'}
    require(set(operations)==set(specs) and len(operations)==len(tx['operations']) and
            result['completed_operations']==len(specs) and not result['pending_operations'],
            'actual remote operation set differs or incomplete')
    for identity,spec in specs.items():
        row=operations[identity]
        require(row['connection']==spec['connection'] and row['verb']==spec['verb'] and
                0<=row['ready_ns']<=row['published_ns']<=row['applied_ns']<=row['request_acked_ns']<=row['completed_ns'],
                'operation used wrong connection or incomplete callback')
        require((spec['verb']=='put' and row['response_ns']==-1) or
                (spec['verb']=='fetch_add' and row['applied_ns']<=row['response_ns']<=row['completed_ns'] and
                 'returned_value' in row), 'atomic response or put completion differs')
    locals_={o['id']:o for o in cfg['operations'] if o['kind']=='local'}
    local_rows={o['id']:o for o in result.get('local_work',[])}
    require(set(locals_)==set(local_rows) and len(local_rows)==len(result.get('local_work',[])), 'local operation set differs')
    if locals_:
        require(result['completed_local_work']==len(locals_) and not result['pending_local_work'], 'local work incomplete')
    for identity,spec in locals_.items():
        row=local_rows[identity]
        require(row['rank']==spec['rank'] and row['duration_ns']==spec['duration_ns'] and
                0<=row['ready_ns']<=row['start_ns'] and row['completed_ns']==row['start_ns']+spec['duration_ns'],
                'local work identity or completion differs')
    require(result['finish_ns']>=max(r['completed_ns'] for r in [*operations.values(),*local_rows.values()]),
            'program finish precedes work completion')
    streams = {s['id']:s for s in net['streams']}
    require(len(streams)==len(net['streams'])==2*len(connections) and
            set(streams)==set(range(2*len(connections))), 'unexpected carrier set')
    capture = result.get('packet_capture',{})
    require(capture.get('complete') and not capture.get('omitted_events') and 'wire_headers' in capture,
            'context check requires complete wire-header capture covering every endpoint')
    events=[dict(zip(capture['columns'],row)) for row in capture['events']]
    headers=[dict(zip(capture['wire_headers']['columns'],row)) for row in capture['wire_headers']['headers']]
    indices=capture['wire_headers']['event_header_indices']
    require(len(indices)==len(events) and all(type(i) is int and 0<=i<len(headers) for i in indices), 'wire-header coverage differs')
    source=[dict(zip(net['source_packet_columns'],row)) for row in net['source_packets']]
    require(all(e['stream'] in streams for e in source), 'unknown source carrier')
    reports=[]
    for binding in mapping['bindings']:
        cid=binding['transaction_connection'];c=connections[cid];count=sum(o['connection']==cid for o in operations.values())
        details=[]
        for reverse in (False,True):
            sid=2*cid+int(reverse);s=streams[sid]
            src,dst=(c['dst'],c['src']) if reverse else (c['src'],c['dst'])
            sn,dn=(c['destination_nic'],c['source_nic']) if reverse else (c['source_nic'],c['destination_nic'])
            port=c['response_port'] if reverse else c['request_port']
            require(all(s[k]==v for k,v in dict(src=src,dst=dst,plane=c['plane'],source_nic=sn,destination_nic=dn,wire_port=port,qp_creations=1).items()),
                    'carrier identity/NIC/port or QP lifetime differs')
            packets=[e for e in source if e['stream']==sid]
            require(all((e['src'],e['dst'],e['nic'])==(src,dst,sn) for e in packets), 'source packet used wrong bound NIC')
            expected=Counter((e['time_ns'],e['packet_uid'],e['sequence'],e['payload_bytes']) for e in packets)
            actual=Counter();receives=0;received_intervals=[]
            for i,e in enumerate(events):
                h=headers[indices[i]]
                if e['ip_protocol']!=17 or h['source_port']!=port:continue
                require(h['ports_present']==1 and h['source_port']==port and h['destination_port']==port,
                        'wire data port differs from persistent binding')
                require(h['packet_uid']==e['packet_uid'] and (e['message_id']<0 or e['message_id']==sid),
                        'captured data identity differs')
                if e['event']==0 and e['node']==src:
                    require(e['port']==sn, 'captured source TX on wrong NIC')
                    actual[e['time_ns'],e['packet_uid'],e['sequence'],e['data_payload_bytes']]+=1
                if e['event']==1 and e['node']==dst:
                    require(e['port']==dn, 'captured destination RX on wrong NIC');receives+=1
                    require(any(p['packet_uid']==e['packet_uid'] and p['sequence']==e['sequence'] and
                                p['payload_bytes']==e['data_payload_bytes'] and p['time_ns']<=e['time_ns'] for p in packets),
                            'destination RX lacks actual source packet')
                    received_intervals.append((e['sequence'],e['sequence']+e['data_payload_bytes']))
            require(actual==expected and len(packets)==s['source_data_packets'], 'source TX capture is incomplete or differs')
            require(s['snd_nxt']==s['snd_una']==s['published_bytes']==s['submitted_bytes'], 'carrier bytes not fully ACKed')
            covered=0
            for begin,end in sorted(received_intervals):
                require(0<=begin<=covered and begin<end<=s['submitted_bytes'], 'destination byte coverage has a gap or invalid range')
                covered=max(covered,end)
            require(covered==s['submitted_bytes'], 'destination byte coverage is incomplete')
            details.append(dict(stream=sid,source_packets=len(packets),destination_rx_packets=receives,
                source_payload_bytes=sum(e['payload_bytes'] for e in packets),published_bytes=s['published_bytes']))
        reports.append(dict(binding=binding['name'],communicator=binding['communicator'],
            context_index=binding['context_index'],operations=count,carriers=details))
    return dict(passed=True,bindings=reports,native_network_executed=True,gpu_visibility_modeled=False,
        hardware_calibrated=False,scope='actual MRT1/native connection binding and endpoint packets; not full modern GIN')


def check(plan, run, output):
    plan,run,output=(Path(p).resolve() for p in (plan,run,output))
    manifest=checked_json(plan/'manifest.json',65536)
    require(manifest['schema_version']=='moe-rdma-context-plan/1', 'wrong context plan schema')
    expected_files={'input.json','topology.json','protocol.json','transactions.json','bindings.json'}
    require(set(manifest['files'])==expected_files, 'wrong context plan members')
    for n,h in manifest['files'].items():require(digest(plan/n)==h, 'context plan hash mismatch')
    cfg,t,p=(checked_json(plan/n,8*2**20) for n in ('input.json','topology.json','protocol.json'))
    program,mapping=compile_program(t,p,cfg)
    require(program==checked_json(plan/'transactions.json',4*2**20) and mapping==checked_json(plan/'bindings.json',2**20), 'context plan derivation differs')
    receipt=checked_json(run/'inputs.json',65536);hashes=receipt['hashes']
    command=receipt['command']
    require(len(command)==5 and all(hashes.get(command[i])==digest(plan/n)
            for i,n in enumerate(('topology.json','protocol.json','transactions.json'),1)),
            'run inputs do not match context plan')
    result=checked_json(run/'result.json',16*2**20)
    value=check_result(dict(program=program,mapping=mapping),result)
    require(not output.exists(), 'context check output already exists');save(output,value);return value
