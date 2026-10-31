"""Execute a verified complete legacy indexed plan on one native network.

This explicitly selects functional zero-cost local visibility/expert copies,
single static registration spans and synchronous batch-four publication.
"""
from collections import Counter
import json
import os
from pathlib import Path
import shutil

from .deepep_indexed import OP_COLUMNS, EVENT_COLUMNS, check as check_indexed
from .deepep_plan import fields, integer, require
from .scaleout_rdma import checked_json, digest, monitored, save, validate_probe
from .scaleout_limits import limits
from .scaleout_stream_routes import validate_nic_routes


def minimum_reference_events(work, payload_bytes):
    """Every carrier frame is work-bounded and every source packet is logged.

    This is a necessary lower bound only: retries, marks and RP/NP timers can
    consume additional entries. Local operations consume no network entries.
    """
    integer(payload_bytes, 1, 65536, 'payload_bytes')
    return sum((32+op[8]+payload_bytes-1)//payload_bytes +
               ((36+payload_bytes-1)//payload_bytes if op[3] != 'data' else 0)
               for op in work['operations'] if op[4] != op[5])


def minimum_posting_span(work):
    """Largest unpublished span per remote context in canonical WQE order."""
    pending=Counter();minimum=1;local_experts=work['experts']//work['ranks']
    for op in work['operations']:
        if op[4]==op[5]:continue  # Self copy/store does not use an IBGDA queue.
        context=op[4],op[5],op[6]%local_experts
        pending[context]+=1;minimum=max(minimum,pending[context])
        if op[3]!='data' or (op[11]+1)%4==0:pending[context]=0
    require(not any(pending.values()),'legacy plan leaves an unpublished posting tail')
    return minimum


def validate_record_capacity(work, protocol, transport):
    minimum = minimum_reference_events(work, protocol['payload_bytes'])
    configured = transport['dcqcn_reference']['max_events']
    require(configured >= minimum,
            f'legacy reference max_events={configured} below mandatory source sends={minimum}; '
            'retries and congestion feedback need additional capacity; no network started')
    return minimum


def lower(directory, binding, topology, protocol):
    """Caller has checked the indexed file set; retain bounded compact records."""
    source = checked_json(directory/'input.json', 1048576)
    model, execution = source['model'], source['execution']
    fields(binding, 'schema_version rank_to_gpu queue_depth publication memory_model', 'legacy binding')
    require(binding['schema_version'] == 'moe-legacy-native-binding/1', 'unsupported legacy binding')
    require(binding['publication'] == 'legacy_sync_batch4/1' and
            binding['memory_model'] == 'single_span_zero_cost_visibility_expert_copy/1',
            'legacy execution requires explicit supported functional memory/publication models')
    require(model['dispatch_format'] == 'bf16', 'legacy native expert copy currently requires BF16')
    ranks = execution['ranks']; experts = model['experts']
    integer(binding['queue_depth'], 1, 4096, 'queue_depth')
    require(binding['queue_depth'] & (binding['queue_depth']-1) == 0, 'queue_depth must be power of two')
    mapping = binding['rank_to_gpu']
    require(isinstance(mapping, list) and len(mapping) == ranks and all(type(g) is int for g in mapping),
            'rank_to_gpu must be a complete list of integer GPU IDs')
    require(len(set(mapping)) == ranks,
            'rank_to_gpu must contain every logical rank exactly once')
    gpus = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    require(len(gpus) in (128, 256, 512), 'legacy native requires 128/256/512 endpoints')
    for gpu in mapping:
        integer(gpu, 0, 511, 'mapped GPU'); require(gpu in gpus, 'mapped GPU absent')
    require(not topology.get('intra_host', {}).get('enabled', False), 'legacy RDMA-only requires local bypass disabled')
    # Do not encode artificial one-byte validation descriptors into executed work.
    messages = [dict(id=s*ranks+d, src=mapping[s], dst=mapping[d], bytes=1, plane=0, ready_ns=0)
                for s in range(ranks) for d in range(ranks) if s != d]
    validate_probe(topology, protocol, messages, match_faults=False, legacy_messages=False, max_probe_messages=1024)
    routes = [dict(id=s*ranks+d, src=mapping[s], dst=mapping[d], source_nic=1, destination_nic=1, plane=0)
              for s in range(ranks) for d in range(ranks) if s != d]
    validate_nic_routes(topology, routes)
    events = []
    with (directory/'events.jsonl').open() as stream:
        for line in stream:
            row = dict(zip(EVENT_COLUMNS, json.loads(line)))
            events.append([row['kind'], row['rank'], row['deps'], row['operation'], row['attributes']])
    operations = []; indices = Counter(); work = 0
    with (directory/'operations.jsonl').open() as stream:
        for line in stream:
            row = dict(zip(OP_COLUMNS, json.loads(line)))
            phase = int(row['phase'] == 'combine')
            key = row['call'], phase, row['src'], row['dst'], row['expert']
            index = indices[key] if row['kind'] == 'data' else 0
            if row['kind'] == 'data': indices[key] += 1
            if row['src'] != row['dst']: work += 1 if row['kind'] == 'data' else 2
            operations.append([row['id'], row['call'], phase, row['kind'], row['src'], row['dst'],
                               row['expert'], row['token'], row['operand_bytes'], row['value'] or 0,
                               row['events'], index])
    require(len(operations) <= 32768 and len(events) <= 140000 and work <= 65536,
            'complete plan exceeds native lifetime budget; no operation may be omitted')
    streams = 2*(ranks-1)*experts
    # Protocol recovery/reference are explicit: no fallback to a weaker transport.
    require(0 < streams <= 16384 and work > 0, 'legacy carrier dimension outside support')
    for rule in protocol.get('faults', []):
        require(rule['message_id'] < streams and rule['node'] in mapping, 'legacy fault outside declared streams/endpoints')
    work = dict(schema_version='moe-legacy-native-execution/1', ranks=ranks, experts=experts,
                hidden_size=model['hidden_size'], dispatch_bytes=2*model['hidden_size']+16,
                max_tokens_per_rank=execution['max_tokens_per_rank'], rank_to_gpu=mapping,
                queue_depth=binding['queue_depth'], operations=operations, events=events,
                limits=dict(streams=streams, work=work, link_transmissions=2000000))
    required=minimum_posting_span(work)
    require(binding['queue_depth']>=required,
            f'legacy queue_depth={binding["queue_depth"]} cannot hold publication span={required}; '
            'ACK cannot free unpublished work')
    return work


def validate_native_inputs(t,p,b,recovery, *, reference_event_cap=100000, shared_resources=False):
    """Validate declarations with an explicit, bounded shared host budget."""
    require(type(shared_resources) is bool,'shared_resources must be boolean')
    fields(b, 'schema_version rank_to_gpu queue_depth publication memory_model', 'legacy binding')
    require(isinstance(b['rank_to_gpu'], list) and len(b['rank_to_gpu']) >= 2 and
            all(type(g) is int for g in b['rank_to_gpu']), 'rank_to_gpu needs at least two integer GPU IDs')
    fields(recovery, 'transport_recovery dcqcn_reference', 'legacy native transport')
    integer(recovery['dcqcn_reference'].get('max_events'), 1, reference_event_cap, 'reference max_events')
    require(recovery['transport_recovery'].get('mode') == 'selective_repeat/1', 'legacy native requires selective repeat')
    r = recovery['transport_recovery']
    require(type(r.get('max_events')) is int and 1 <= r['max_events'] <= 500000, 'legacy recovery event budget')
    # All other recovery/reference field checks retain the existing validators;
    # the larger complete-plan record budget is admitted only here.
    # Reuse the regular validator on descriptors only; these bytes are not sent.
    descriptor = dict(schema_version='moe-rdma-stream-work/2', limits=dict(max_streams=2, max_work=2),
        streams=[], work=[dict(id=0, stream=0, bytes=1, after=[])])
    first = b['rank_to_gpu'][:2]
    descriptor['streams'] = [dict(id=i, src=first[i], dst=first[1-i], plane=0,
        source_nic=1, destination_nic=1, wire_port=20000+i, queue_depth=b['queue_depth']) for i in range(2)]
    from copy import deepcopy
    descriptor.update(deepcopy(recovery)); descriptor['transport_recovery']['max_events'] = min(100000, r['max_events'])
    descriptor['dcqcn_reference']['max_events']=min(100000,recovery['dcqcn_reference']['max_events'])
    from .scaleout_streams import validate as validate_streams
    descriptor_protocol = dict(p)
    descriptor_protocol.pop('faults', None)
    validate_streams(t, descriptor_protocol, descriptor)
    budget = limits(p)
    maximum=(180,3*2**30,6*2**30,512*2**20) if shared_resources else (120,2*2**30,4*2**30,256*2**20)
    require(all(budget[key]<=bound for key,bound in zip(
        ('wall_seconds','rss_bytes','address_bytes','result_json_bytes'),maximum)),
        'legacy host budget exceeds '+('shared180s/3GiB RSS/6GiB address/512MiB result' if shared_resources
                                     else 'single120s/2GiB RSS/4GiB address/256MiB result'))
    return budget


def run(plan, binding, topology, protocol, transport, backend, output, *, validate_only=False):
    require(type(validate_only) is bool, 'validate_only must be boolean')
    plan, binding, topology, protocol, transport, backend, output = map(lambda p: Path(p).resolve(),
        (plan, binding, topology, protocol, transport, backend, output))
    require(not output.exists() and output.parent.is_dir(), 'legacy output must be a new directory with existing parent')
    require(backend.is_file() and os.access(backend, os.X_OK), 'pass legacy_deepep executable from rdma-build')
    require(shutil.disk_usage(output.parent).free >= 8*2**30+512*2**20, 'legacy output needs 8GiB reserve')
    t = checked_json(topology/'topology.json', 8388608); p = checked_json(protocol, 65536)
    b = checked_json(binding, 65536); recovery = checked_json(transport, 65536)
    budget=validate_native_inputs(t,p,b,recovery)
    output.mkdir()
    # The existing checker compares every generated line, not hashes alone.
    checked = check_indexed(plan, output/'plan-check.json')
    require(checked['passed'], 'legacy source plan check failed')
    work = lower(plan, b, t, p); work.update(recovery)
    minimum_events = validate_record_capacity(work, p, recovery)
    save(output/'execution.json', work)
    command = [backend, topology/'topology.json', protocol, output/'execution.json', output/'result.json']
    receipt = dict(command=list(map(str, command)),
        source_plan_manifest_sha256=digest(plan/'manifest.json'),
        hashes={str(path): digest(path) for path in (binding, topology/'topology.json', protocol, transport, backend, output/'execution.json')},
        endpoint_count=sum(n['kind']=='gpu' for n in t['nodes']), ep_size=work['ranks'],
        minimum_reference_source_events=minimum_events,
        minimum_posting_span=minimum_posting_span(work),
        data_active_ranks=sum(bool(tokens) for tokens in checked_json(plan/'input.json', 1048576)['calls'][0]['tokens']),
        physical_gpu_executed=False)
    if validate_only:
        receipt['planned_native_command'] = receipt.pop('command')
        receipt.update(validation_only=True, native_network_executed=False)
    save(output/'inputs.json', receipt)
    if validate_only:
        source = checked_json(plan/'input.json', 1048576)
        validation = dict(schema_version='moe-legacy-native-validation/1',passed=True,
            validation_only=True, native_admission_executed=False,native_network_executed=False,
            endpoints=receipt['endpoint_count'],ep_size=work['ranks'],experts=work['experts'],
            experts_per_rank=work['experts']//work['ranks'],hidden_size=work['hidden_size'],
            dispatch_format='bf16',calls=len(source['calls']),
            per_call_tokens=[sum(map(len,call['tokens'])) for call in source['calls']],
            per_call_data_active_ranks=[sum(map(bool,call['tokens'])) for call in source['calls']],
            rank_to_gpu=b['rank_to_gpu'],queue_depth=b['queue_depth'],
            logical_operations=len(work['operations']),logical_events=len(work['events']),
            carrier_streams=work['limits']['streams'],carrier_work=work['limits']['work'],
            minimum_reference_source_events=minimum_events,
            configured_reference_events=recovery['dcqcn_reference']['max_events'],
            minimum_posting_span=minimum_posting_span(work),
            scope='Python/source-plan checks only; no native admission, Simulator, packets, completion or hardware calibration')
        save(output/'validation.json', validation)
        return validation
    monitored(command, output/'execution-monitor', [output], runtime=budget)
    result = checked_json(output/'result.json', budget['result_json_bytes'])
    require(result.get('passed') and result['legacy']['finished'], 'incomplete legacy native execution')
    return dict(passed=True, output=str(output), logical_operations=len(work['operations']),
                logical_events=len(work['events']), endpoints=len({n['id'] for n in t['nodes'] if n['kind']=='gpu'}),
                ep_size=work['ranks'], registered_contexts=result['legacy']['registered_contexts'],
                hardware_calibrated=False, numerical_experts_executed=False)
