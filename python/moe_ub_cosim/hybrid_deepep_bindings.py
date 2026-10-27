"""Source-derived Hybrid Dispatch resource bindings; no transport execution.

Serialization order is not WQE reservation order. Combine channels, cross-peer
warp synchronizations, GPU visibility and provider costs remain unresolved.
"""
import hashlib
from pathlib import Path

from .deepep_plan import encoded, fields, integer, read, require
from .hybrid_deepep_plan import compile_plan, load_plan
from .modern_deepep_plan import REVISION, NCCL_REVISION, context_assignment, qp_mode

MAX_INPUT, MAX_OUTPUT = 2**20, 16*2**20
SCOPE = dict(network_executed=False, hardware_calibrated=False,
             complete_collective_dag=False, combine_bound=False,
             reservation_order_resolved=False, physical_nics_discovered=False)


def validate_provider(provider, config):
    fields(provider, 'schema_version devcomm backend nic_handler call_contexts '
           'requested_contexts connections gin_connection_stride local_devices limits', 'Hybrid provider')
    require(provider['schema_version'] == 'moe-hybrid-provider-input/1', 'unsupported Hybrid provider schema')
    name = provider['devcomm']
    require(isinstance(name, str) and 1 <= len(name) <= 64, 'invalid devcomm lifetime name')
    require(provider['backend'] == 'nccl_gin_gdaki', 'unsupported Hybrid provider backend')
    require(provider['nic_handler'] in ('gpu_sm_db', 'gpu_sm_no_dbr'),
            'Hybrid bindings require an explicit DB or NO_DBR handler')
    for key, maximum in (('call_contexts', 64), ('requested_contexts', 64), ('connections', 4)):
        integer(provider[key], 1, maximum, key)
    require(provider['call_contexts'] <= provider['requested_contexts'], 'call contexts exceed allocation request')
    ranks = sum(len(d['ranks']) for d in config['deployment']['domains'])
    scaleup = len(config['deployment']['rails'])
    stride = provider['gin_connection_stride']
    require(type(stride) is int and stride in (1, scaleup), 'unsupported GIN connection stride')
    devices = provider['local_devices']
    require(isinstance(devices, list) and len(devices) == ranks, 'require each rank local device declaration')
    for row in devices:
        require(isinstance(row, list) and 1 <= len(row) <= 4, 'invalid local device table')
        for device in row:
            integer(device, 0, 65535, 'local device ID')
        require(len(set(row)) == len(row), 'repeated local device ID')
    require(config['execution']['num_sms'] >= 2, 'Hybrid host launch requires at least two SMs')
    limits = provider['limits']
    fields(limits, 'max_bindings max_resources max_input_bytes max_output_bytes', 'Hybrid provider limits')
    for key, maximum in (('max_bindings', 32768), ('max_resources', 8192),
                         ('max_input_bytes', MAX_INPUT), ('max_output_bytes', MAX_OUTPUT)):
        integer(limits[key], 1, maximum, key)


def compile_bindings(config, provider):
    plan = compile_plan(config)
    validate_provider(provider, config)
    limits = provider['limits']
    require(len(encoded(config))+len(encoded(provider)) <= limits['max_input_bytes'], 'Hybrid binding input budget exceeded')
    launch = config['execution']
    resources, bindings, streams, counts = [], [], [], []
    lookup, seen = {}, set()

    def add(identity, kind, source, target, call, channel, operand_bytes,
            transport, aggregate=None, logical_id=None, **metadata):
        require(identity not in seen and len(bindings) < limits['max_bindings'], 'Hybrid binding identity/budget exceeded')
        seen.add(identity)
        row = dict(id=identity, logical_id=logical_id or identity, kind=kind, source=source,
                   target=target, call=call, channel=channel, operand_bytes=operand_bytes,
                   transport=transport, aggregate_requests=aggregate, resource=None, **metadata)
        if transport == 'gin':
            if channel is None:
                index, sharing = 0, 'cta'
                if provider['call_contexts'] == 1:
                    sharing = 'gpu'  # Hybrid host requires multiple SMs.
                sm, warp = 0, None
            else:
                sm, warp = divmod(channel, launch['scaleout_warps'])
                index, sharing = qp_mode(sm, warp, launch['num_sms'], provider['call_contexts'],
                                          launch['scaleout_warps'], True)
            assignment = context_assignment(index, provider, source)
            peer = target//provider['gin_connection_stride']
            key = (provider['devcomm'], source, assignment['connection'], assignment['local_context'],
                   assignment['local_device'], peer)
            if key not in lookup:
                require(len(resources) < limits['max_resources'], 'Hybrid resource budget exceeded')
                name = 'q'+str(len(resources))
                lookup[key] = name
                resources.append(dict(id=name, devcomm=provider['devcomm'], source=source, target=target,
                    provider_connection=assignment['connection'], local_context=assignment['local_context'],
                    local_device=assignment['local_device'], provider_peer=peer,
                    world_peer=target, rail_peer=target//plan['ranks_per_domain']))
            row.update(resource=lookup[key], context_index=index, sharing=sharing, sm=sm, warp=warp)
        bindings.append(row)
        return identity

    for count in plan['count_blocks']:
        add(count['id'], count['kind']+'_count', count['source'], count['target'],
            count['call'], None, count['operand_bytes'], 'gin', count['kind'] == 'rank',
            values=count['values'])
        if count['kind'] == 'rank':
            counts.append(dict(source=count['source'], target=count['target'], call=count['call'],
                operations=[count['id'], count['id'].removesuffix('rank_count')+'expert_count']))

    data = {row['id']: row for row in plan['dispatch']}
    for channel in plan['channels']:
        src, dst = channel['source'], channel['target']
        local = src == dst
        sequence, start, previous = [], 0, 0
        for index, publication in enumerate(channel['publications']):
            end = publication['tail']
            for name in channel['transfers'][start:end]:
                row = data[name]
                sequence.append(add(name, 'token_data', src, dst, channel['call'], channel['channel'],
                    row['token_operand_bytes'], 'local_copy' if local else 'gin', None if local else True,
                    token=row['token'], topk_positions=row['topk_positions'],
                    domain_channel_slot=row['domain_channel_slot']))
            value = (int(publication['finished']) << 32) | end
            name = channel['id']+'.tail'+str(index)
            sequence.append(add(name, 'finish' if publication['finished'] else 'tail', src, dst,
                channel['call'], channel['channel'], 8, 'local_release_atomic' if local else 'gin',
                None if local else False, logical_id=channel['id'], publication=index,
                tail=end, finished=publication['finished'], add_value=value-previous,
                required_data_prefix=end))
            start, previous = end, value
        streams.append(dict(id=channel['id'], operations=sequence,
                            order='per_peer_local_api_order_not_remote_completion'))
    connections = provider['connections']
    result = dict(schema_version='moe-hybrid-dispatch-bindings/1',
        library_revision=REVISION, nccl_revision=NCCL_REVISION, scope=dict(SCOPE),
        allocated_contexts=((provider['requested_contexts']+connections-1)//connections)*connections,
        serialization_order='not_reservation_or_network_order',
        resources=resources, bindings=bindings, data_streams=streams,
        count_sequences=counts, combine_logical_rows=len(plan['combine']))
    require(len(encoded(result)) <= limits['max_output_bytes'], 'Hybrid binding output budget exceeded')
    return result


def manifest(files):
    return dict(schema_version='moe-hybrid-bindings-manifest/1', library_revision=REVISION,
                nccl_revision=NCCL_REVISION, scope=dict(SCOPE),
                sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())})


def execute(plan_path, provider_path, output):
    output = Path(output)
    require(not output.exists(), 'Hybrid binding output already exists')
    _, config = load_plan(plan_path)
    raw, saved = read(Path(plan_path)/'input.json', MAX_INPUT)
    require(saved == config, 'Hybrid logical input changed during binding')
    provider_raw, provider = read(Path(provider_path), MAX_INPUT)
    bound = compile_bindings(config, provider)
    require(len(raw)+len(provider_raw) <= provider['limits']['max_input_bytes'], 'Hybrid binding saved input budget exceeded')
    files = {'input.json': raw, 'provider.json': provider_raw, 'bindings.json': encoded(bound)}
    record = manifest(files)
    require(sum(map(len, files.values()))+len(encoded(record)) <= provider['limits']['max_output_bytes'],
            'Hybrid binding total output budget exceeded')
    output.mkdir(parents=True)
    for name, content in files.items():
        (output/name).write_bytes(content)
    (output/'manifest.json').write_bytes(encoded(record))
    return record


def load_bindings(directory):
    directory = Path(directory)
    raw, config = read(directory/'input.json', MAX_INPUT)
    provider_raw, provider = read(directory/'provider.json', MAX_INPUT)
    bound_raw, bound = read(directory/'bindings.json', MAX_OUTPUT)
    record_raw, _ = read(directory/'manifest.json', MAX_INPUT)
    files = {'input.json': raw, 'provider.json': provider_raw, 'bindings.json': bound_raw}
    require(record_raw == encoded(manifest(files)), 'Hybrid binding manifest/hash/scope mismatch')
    require(bound_raw == encoded(compile_bindings(config, provider)), 'Hybrid binding differs from saved input semantics')
    require(len(raw)+len(provider_raw) <= provider['limits']['max_input_bytes'], 'Hybrid binding saved input budget exceeded')
    require(sum(map(len, files.values()))+len(record_raw) <= provider['limits']['max_output_bytes'],
            'Hybrid binding saved output budget exceeded')
    return bound, config, provider
