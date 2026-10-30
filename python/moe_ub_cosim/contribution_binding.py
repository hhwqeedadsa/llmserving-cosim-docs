"""Bind declared simulation contributions to the same service's actual events.

Only original attempts are observed. No backup, timers, altered gates, packets,
weights or activation tensors are introduced. Shared message references are
logical obligations, not independent physical allocations.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .contribution_attempts import ContributionAttempts, identity
from .execution_events import ExecutionObserver, configuration as event_configuration
from .scaleout_topology import fields, integer, require


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def configuration(config):
    if 'contribution_tracking' not in config:return None
    value = config['contribution_tracking']
    fields(value, 'schema_version namespace max_invocations max_bundles max_lifecycle_events max_report_bytes', 'contribution tracking')
    require(value['schema_version'] == 'moe-contribution-binding/1', 'unsupported contribution binding schema')
    identity(value['namespace'])
    for name, maximum in [('max_invocations', 4096), ('max_bundles', 32768),
                          ('max_lifecycle_events', 524288), ('max_report_bytes', 33554432)]:
        integer(value[name], name, 1, maximum)
    require(config.get('schema_version') in ('moe-scaleout-service/1', 'moe-scaleout-service/2') and 'route_replay' in config and
            event_configuration(config) is not None and 'transport' in config and
            config.get('phase_completion', {}).get('mode') == 'ep_transport_flush/1' and
            config.get('token_transport', {}).get('mode') == 'deepep_direct_bf16/1' and
            config['token_transport'].get('combine') == 'rank_reduced/1',
            'contribution tracking requires ready-KV replay, execution events, persistent transport and rank-reduced phase completion')
    return value


def preflight(config, replay):
    """Count actual source choices before launching any backend process."""
    option = configuration(config)
    if option is None:return None
    calls = bundles = lifecycle = 0
    for group in config['parallel']['groups']:
        for step in range(config['workload']['decode_steps']):
            for layer in range(config['model']['layers']):
                for token in replay.routes(group, step, layer):
                    owners = {replay.owner(group, layer, e) for e in token['experts']}
                    calls += 1;bundles += len(owners)
                    lifecycle += sum(8 if owner == token['rank'] else 12 for owner in owners)
    if 'inflight_helpers' in config:lifecycle += 12*config['inflight_helpers']['max_attempts']
    require(calls <= option['max_invocations'] and bundles <= option['max_bundles'] and
            lifecycle <= option['max_lifecycle_events'], 'contribution tracking input exceeds cumulative budget')
    return dict(invocations=calls, bundles=bundles, lifecycle_events=lifecycle)


class ContributionBinding:
    """Online consumer. All time values come from validated native callbacks."""
    def __init__(self, config, replay, observer):
        self.config = config
        self.option = configuration(config)
        require(self.option is not None and replay is not None and isinstance(observer, ExecutionObserver), 'missing contribution binding inputs')
        self.replay, self.observer = replay, observer
        self.calls, self.actions = {}, defaultdict(list)
        self.bundles = self.lifecycle_events = self.reserved_events = 0
        if 'inflight_helpers' in config:self.reserved_events = 12*config['inflight_helpers']['max_attempts']
        self.execution_sha256 = fingerprint(dict(schema_version='moe-declared-execution/1',
            model=config['model'], compute=config['compute'], token_transport=config['token_transport']))

    def register(self, wave):
        graph = wave['graph_id']
        group = self.config['parallel']['groups'][wave['group_index']]
        require(wave['rank_ids'] == group and len(wave['routes']) == self.config['model']['layers'], 'binding graph deployment differs')
        plan, actions = {}, defaultdict(list)
        nodes = {(int(rank), n['name']): n['id'] for rank, rows in wave['rank_nodes'].items() for n in rows}
        messages = {(m['layer'], m['phase'], m['src'], m['dst']): m['id'] for m in wave['messages']}
        reserved = bundles = 0

        def action(key, call, bundle_id, kind, **kwargs):
            actions[key].append((call, dict(kind=kind, attempt='original.'+bundle_id, **kwargs)))

        def nk(kind, rank, node):return kind, graph, rank, node
        def mk(kind, rank, message):return kind, graph, rank, message

        for layer in wave['routes']:
            lid = layer['layer']
            require(layer['tokens'] == self.replay.routes(group, wave['decode_step'], lid), 'binding routes differ from source requests')
            for token in layer['tokens']:
                src, source = token['rank'], token['source']
                request = self.replay.records[source['request_id']]
                request_key = fingerprint(dict(file_id=source['file_id'], sha256=source['sha256'],
                    request_id=source['request_id'], original_path=request['original_path']))
                call = f'{graph}.l{lid}.r{src}.s{token["slot"]}'
                require(call not in self.calls and call not in plan, 'contribution call identity reused')
                pack = nodes[src, f'L{lid}.pack']
                invocation = dict(request_id='request.'+request_key, decode_step=wave['decode_step'],
                    source_layer=source['source_layer_id'],
                    token_coordinate=dict(kind='route_decode_entry/1', source_entry=source['source_entry'], source_token_row=source['source_token_row']),
                    execution_sha256=self.execution_sha256,
                    input_identity=dict(kind='simulation_lineage/1', namespace=self.option['namespace'], graph_id=graph,
                                        producer_rank=src, producer_node=pack),
                    routing_sha256=fingerprint(dict(experts=token['experts'], source_layer=source['source_layer_id'])))
                owners = defaultdict(list)
                for expert in token['experts']:owners[self.replay.owner(group, lid, expert)].append(expert)
                providers = set(owners)
                if 'inflight_helpers' in self.config:
                    for candidate in wave['helper_candidates']:
                        if candidate['call']==call:providers.add(candidate['provider'])
                spec = dict(schema_version='moe-contribution-attempts/2', invocation=invocation,
                    expected_experts=sorted(token['experts']),
                    bundles=[dict(id=f'r{rank}', experts=sorted(es)) for rank, es in sorted(owners.items())],
                    replicas=[dict(rank=rank, execution_sha256=self.execution_sha256,
                        experts=[e for e in range(self.config['model']['experts']) if self.replay.owner(
                            next(g for g in self.config['parallel']['groups'] if rank in g), lid, e) == rank]) for rank in sorted(providers)],
                    limits=dict(max_attempts_per_bundle=2 if 'inflight_helpers' in self.config else 1,
                                max_resources=(6 if 'inflight_helpers' in self.config else 3)*len(owners),
                                max_events=192 if 'inflight_helpers' in self.config else 96))
                runtime = ContributionAttempts(spec)
                binding = dict(graph_id=graph, source_rank=src, slot=token['slot'], logical_layer=lid,
                               source=dict(source), bundles=[])
                for rank, es in sorted(owners.items()):
                    bundle = f'r{rank}'
                    graph_ref = 'graph.'+graph+'.'+bundle
                    action(nk('node_issue', src, pack), call, bundle, 'start', bundle=bundle, rank=rank)
                    action(nk('node_issue', src, pack), call, bundle, 'resource_open', resource=graph_ref)
                    expert_node = nodes[rank, f'L{lid}.experts']
                    producer = nodes[rank, f'L{lid}.combine_reduce']
                    start_kind = 'node_compute_start' if self.observer.actual_compute_starts else 'node_issue'
                    action(nk(start_kind, rank, expert_node), call, bundle, 'compute_begin')
                    action(nk('node_complete', rank, expert_node), call, bundle, 'compute_done')
                    dispatch = combine = None
                    if src == rank:
                        action(nk('node_complete', src, pack), call, bundle, 'input_ready')
                        result_key = nk('node_complete', rank, producer)
                        reserved += 8
                    else:
                        dispatch = messages[lid, 'dispatch', src, rank]
                        combine = messages[lid, 'combine', rank, src]
                        for phase, message, sender in [('dispatch', dispatch, src), ('combine', combine, rank)]:
                            ref = f'{phase}.{message}.{bundle}'
                            action(mk('message_send_post', sender, message), call, bundle, 'resource_open', resource=ref)
                            action(mk('message_send_complete', sender, message), call, bundle, 'resource_close', resource=ref)
                        action(mk('message_recv_complete', rank, dispatch), call, bundle, 'input_ready')
                        result_key = mk('message_recv_complete', src, combine)
                        reserved += 12
                    action(result_key, call, bundle, 'result', key_sha256=runtime.bundle_key(bundle))
                    action(('graph_retired', graph), call, bundle, 'resource_close', resource=graph_ref)
                    action(('graph_retired', graph), call, bundle, 'retire')
                    binding['bundles'].append(dict(id=bundle, rank=rank, experts=sorted(es), pack_node=pack,
                        expert_node=expert_node, result_producer_node=producer, dispatch_message=dispatch, combine_message=combine))
                bundles += len(owners)
                plan[call] = dict(spec=spec, runtime=runtime, binding=binding)
        require(len(self.calls)+len(plan) <= self.option['max_invocations'] and
                self.bundles+bundles <= self.option['max_bundles'] and
                self.reserved_events+reserved <= self.option['max_lifecycle_events'], 'contribution tracking budget exceeded')
        self.observer.register(wave)
        self.calls.update(plan)
        self.bundles += bundles
        self.reserved_events += reserved
        for key, value in actions.items():self.actions[key].extend(value)

    def accept(self, event):
        self.observer.accept(event)
        kind = event['kind']
        if kind.startswith('node_'):key = kind, event['graph_id'], event['rank'], event['node']
        elif kind.startswith('message_'):key = kind, event['graph_id'], event['rank'], event['message']
        elif kind == 'graph_retired':key = kind, event['graph_id']
        else:key = (kind,)
        for call, action in self.actions.get(key, []):
            require(self.lifecycle_events < self.option['max_lifecycle_events'], 'contribution lifecycle event budget exceeded')
            self.calls[call]['runtime'].apply(dict(action, time_ns=event['time_ns']))
            self.lifecycle_events += 1

    def records(self):
        for call, row in sorted(self.calls.items()):
            yield dict(schema_version='moe-bound-contribution/1', call=call, binding=row['binding'],
                       spec=row['spec'], state=row['runtime'].snapshot())

    def summary(self):
        states = [row['runtime'].snapshot() for row in self.calls.values()]
        return dict(schema_version='moe-contribution-binding-report/1', configuration=dict(self.option),
            invocations=len(states), bundles=self.bundles, lifecycle_events=self.lifecycle_events,
            reserved_lifecycle_events=self.reserved_events,
            logical_complete_calls=sum(s['logical_complete_ns'] is not None for s in states),
            retired_calls=sum(s['declared_call_retirable'] for s in states),
            source_events=self.observer.count, service_drained_ns=self.observer.drained_ns,
            scope='actual_service_callbacks_simulation_lineage_original_attempts_no_tensors_or_recovery')

    def write(self, directory):
        directory = Path(directory)
        size = 0
        with (directory/'contribution_calls.jsonl').open('xb') as stream:
            for row in self.records():
                raw = (json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True)+'\n').encode()
                require(size+len(raw) <= self.option['max_report_bytes'], 'contribution report byte budget exceeded')
                stream.write(raw);size += len(raw)
        with (directory/'contribution_summary.json').open('x') as stream:
            json.dump(self.summary(), stream, indent=2);stream.write('\n')


def audit(directory, frontend):
    from .scaleout_rdma import checked_json
    from .scaleout_replay import Replay
    directory = Path(directory)
    config = checked_json(directory/'input.json', 2**20)
    option = configuration(config)
    if option is None:
        require(not (directory/'contribution_calls.jsonl').exists() and not (directory/'contribution_summary.json').exists(),
                'undeclared contribution tracking output')
        return None
    binding = ContributionBinding(config, Replay(config, directory), ExecutionObserver(event_configuration(config)))
    for row in frontend['history']:
        binding.register(checked_json(directory/'graphs'/row['graph_id']/'wave.json', 268435456))
    with (directory/'execution_events.jsonl').open() as stream:
        for line in stream:
            require(line.endswith('\n') and len(line.encode()) <= 1024, 'invalid contribution source event frame')
            binding.accept(json.loads(line))
    path = directory/'contribution_calls.jsonl'
    require(path.stat().st_size <= option['max_report_bytes'], 'contribution report exceeds byte budget')
    with path.open('rb') as stream:
        for row in binding.records():
            expected = (json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=True)+'\n').encode()
            require(stream.readline(len(expected)+1) == expected, 'bound contribution differs from actual service events')
        require(stream.read(1) == b'', 'extra bound contribution record')
    summary = binding.summary()
    require(checked_json(directory/'contribution_summary.json', 65536) == summary and summary['invocations'] > 0 and
            summary['logical_complete_calls'] == summary['retired_calls'] == summary['invocations'] and
            summary['lifecycle_events'] == summary['reserved_lifecycle_events'] and summary['service_drained_ns'] is not None,
            'incomplete or mismatched contribution binding summary')
    return summary
