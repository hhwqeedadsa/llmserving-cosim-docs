"""Untimed contribution/attempt lifecycle, driven only by caller callbacks.

This core does not run ASTRA, send packets, authenticate results, verify tensors,
or release provider resources. A logical winner never synthesizes an RDMA ACK.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


MAX_INPUT = 2**20


def require(condition, message):
    if not condition:
        raise ValueError('Contribution attempts: '+message)


def fields(value, names, label):
    require(type(value) is dict and set(value) == set(names.split()), 'invalid '+label+' fields')


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high, 'invalid '+label)


def identity(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is not None,
            'invalid identity')


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'invalid SHA-256 identity')


def experts(value, maximum=8):
    require(type(value) is list and 1 <= len(value) <= maximum, 'invalid expert set')
    for expert in value:
        integer(expert, 0, 255, 'expert')
    require(value == sorted(set(value)), 'expert set must be sorted and unique')


class ContributionAttempts:
    """One token/layer's declared expert cover, potentially split by owner rank.

    All mutations are apply(event). Rejected events leave the complete state
    unchanged. Ties follow caller callback order on its common clock, not rank
    or attempt priority. Keys check declared identities, not numerical equality.
    """

    def __init__(self, spec):
        fields(spec, 'schema_version invocation expected_experts bundles replicas limits', 'specification')
        require(spec['schema_version'] in ('moe-contribution-attempts/1', 'moe-contribution-attempts/2'), 'unsupported specification schema')
        context = spec['invocation']
        modeled = spec['schema_version'] == 'moe-contribution-attempts/2'
        fields(context, 'request_id decode_step source_layer execution_sha256 routing_sha256 '+
               ('token_coordinate input_identity' if modeled else 'token_position input_sha256'), 'invocation')
        identity(context['request_id'])
        for name in ('decode_step',) if modeled else ('decode_step', 'token_position'):
            integer(context[name], 0, 2**31-1, name)
        integer(context['source_layer'], 0, 127, 'original source layer')
        for name in ('execution_sha256', 'routing_sha256') if modeled else ('execution_sha256', 'input_sha256', 'routing_sha256'):
            digest(context[name])
        if modeled:
            coordinate = context['token_coordinate']
            fields(coordinate, 'kind source_entry source_token_row', 'token coordinate')
            require(coordinate['kind'] == 'route_decode_entry/1', 'unsupported token coordinate')
            integer(coordinate['source_entry'], 1, 2**31-1, 'Decode source entry')
            integer(coordinate['source_token_row'], 0, 2**31-1, 'source token row')
            lineage = context['input_identity']
            fields(lineage, 'kind namespace graph_id producer_rank producer_node', 'simulation input lineage')
            require(lineage['kind'] == 'simulation_lineage/1', 'unsupported simulation input identity')
            identity(lineage['namespace']);identity(lineage['graph_id'])
            integer(lineage['producer_rank'], 0, 511, 'input producer rank')
            integer(lineage['producer_node'], 1, 2**64-1, 'input producer node')
        experts(spec['expected_experts'])
        require(type(spec['bundles']) is list and 1 <= len(spec['bundles']) <= 8, 'invalid bundle count')
        self._bundles, cover = {}, set()
        for bundle in spec['bundles']:
            fields(bundle, 'id experts', 'bundle')
            identity(bundle['id'])
            experts(bundle['experts'])
            require(bundle['id'] not in self._bundles and not cover.intersection(bundle['experts']),
                    'duplicate bundle or overlapping expert cover')
            cover.update(bundle['experts'])
            encoded = json.dumps(dict(invocation=context, experts=bundle['experts']), sort_keys=True,
                                 separators=(',', ':'), ensure_ascii=True).encode()
            self._bundles[bundle['id']] = dict(experts=tuple(bundle['experts']), key_sha256=hashlib.sha256(encoded).hexdigest())
        require(cover == set(spec['expected_experts']), 'bundles must cover exactly the expected experts')
        require(type(spec['replicas']) is list and 1 <= len(spec['replicas']) <= 512, 'invalid replica count')
        self._replicas = {}
        for replica in spec['replicas']:
            fields(replica, 'rank execution_sha256 experts', 'replica')
            integer(replica['rank'], 0, 511, 'replica rank')
            digest(replica['execution_sha256'])
            experts(replica['experts'], 256)
            require(replica['rank'] not in self._replicas, 'duplicate replica rank')
            self._replicas[replica['rank']] = deepcopy(replica)
        fields(spec['limits'], 'max_attempts_per_bundle max_resources max_events', 'limits')
        for name, maximum in [('max_attempts_per_bundle', 8), ('max_resources', 4096), ('max_events', 8192)]:
            integer(spec['limits'][name], 1, maximum, name)
        self._spec = deepcopy(spec)
        self._attempts, self._resources, self._winners, self._events = {}, {}, {}, []
        self._clock, self._complete = 0, None

    def bundle_key(self, bundle):
        identity(bundle)
        require(bundle in self._bundles, 'unknown bundle')
        return self._bundles[bundle]['key_sha256']

    def apply(self, event):
        require(type(event) is dict and 'kind' in event, 'invalid event')
        kind = event['kind']
        arguments = {
            'start': 'bundle rank', 'resource_open': 'resource', 'resource_close': 'resource',
            'input_ready': '', 'compute_begin': '', 'compute_done': '',
            'result': 'key_sha256', 'fail': 'reason', 'retire': '',
        }
        require(type(kind) is str and kind in arguments, 'unknown event kind')
        fields(event, 'kind time_ns attempt '+arguments[kind], 'event')
        now, attempt = event['time_ns'], event['attempt']
        integer(now, self._clock, 2**63-1, 'nondecreasing callback time')
        identity(attempt)
        require(len(self._events) < self._spec['limits']['max_events'], 'event budget exhausted')
        resource_change, winner = None, None
        if kind == 'start':
            identity(event['bundle'])
            bundle = event['bundle']
            require(bundle in self._bundles and bundle not in self._winners, 'unknown or already committed bundle')
            integer(event['rank'], 0, 511, 'attempt rank')
            rank = event['rank']
            require(attempt not in self._attempts, 'attempt identity reused')
            require(sum(a['bundle'] == bundle for a in self._attempts.values()) <
                    self._spec['limits']['max_attempts_per_bundle'], 'attempt budget exhausted')
            require(rank in self._replicas, 'unknown replica rank')
            replica = self._replicas[rank]
            require(replica['execution_sha256'] == self._spec['invocation']['execution_sha256'] and
                    set(self._bundles[bundle]['experts']) <= set(replica['experts']), 'replica version or expert cover mismatch')
            state = dict(bundle=bundle, rank=rank, started_ns=now, input_ready_ns=None,
                compute_begin_ns=None, compute_done_ns=None, result_ready_ns=None,
                failed_ns=None, failure_reason=None, retired_ns=None, resources=set())
            outcome = 'started'
        else:
            require(attempt in self._attempts, 'unknown attempt')
            state = dict(self._attempts[attempt], resources=set(self._attempts[attempt]['resources']))
            if kind != 'result':
                require(state['retired_ns'] is None, 'callback after attempt retirement')
            if kind == 'resource_open':
                identity(event['resource'])
                name = event['resource']
                require(state['failed_ns'] is None and state['result_ready_ns'] is None, 'cannot acquire resources after attempt settlement')
                require(name not in self._resources, 'resource identity reused')
                require(len(self._resources) < self._spec['limits']['max_resources'], 'resource budget exhausted')
                resource_change = name, dict(attempt=attempt, opened_ns=now, closed_ns=None)
                state['resources'].add(name)
                outcome = 'resource_opened'
            elif kind == 'resource_close':
                identity(event['resource'])
                name = event['resource']
                require(name in self._resources and name in state['resources'], 'unknown, foreign or already closed resource')
                require(self._resources[name]['attempt'] == attempt, 'resource owner differs')
                resource_change = name, dict(self._resources[name], closed_ns=now)
                state['resources'].remove(name)
                outcome = 'resource_closed'
            elif kind == 'input_ready':
                require(state['input_ready_ns'] is None, 'input callback repeated')
                state['input_ready_ns'], outcome = now, 'input_ready'
            elif kind == 'compute_begin':
                require(state['input_ready_ns'] is not None and state['compute_begin_ns'] is None and
                        state['failed_ns'] is None, 'compute needs input and an eligible unstarted attempt')
                state['compute_begin_ns'], outcome = now, 'compute_began'
            elif kind == 'compute_done':
                require(state['compute_begin_ns'] is not None and state['compute_done_ns'] is None,
                        'compute completion needs a prior start and cannot repeat')
                # Work already issued may finish after an actual failure/cancel.
                state['compute_done_ns'], outcome = now, 'compute_done'
            elif kind == 'fail':
                require(event['reason'] in ('provider_error', 'cancelled', 'deadline') and type(event['reason']) is str,
                        'invalid failure reason')
                require(state['failed_ns'] is None and state['result_ready_ns'] is None, 'attempt already settled')
                state.update(failed_ns=now, failure_reason=event['reason'])
                outcome = 'failed'
            elif kind == 'retire':
                require((state['failed_ns'] is not None or state['result_ready_ns'] is not None) and not state['resources'] and
                        (state['compute_begin_ns'] is None or state['compute_done_ns'] is not None),
                        'retirement needs settlement, completed in-flight compute and all provider resources closed')
                state['retired_ns'], outcome = now, 'retired'
            else:  # result: check identity even for late/duplicate arrivals.
                digest(event['key_sha256'])
                require(event['key_sha256'] == self.bundle_key(state['bundle']), 'result identity differs from the declared invocation/expert bundle')
                if state['retired_ns'] is not None:
                    outcome = 'late_retired'
                elif state['failed_ns'] is not None:
                    outcome = 'late_failed'
                elif state['result_ready_ns'] is not None:
                    outcome = 'duplicate'
                else:
                    require(state['compute_done_ns'] is not None, 'result precedes expert computation')
                    state['result_ready_ns'] = now
                    if state['bundle'] in self._winners:
                        outcome = 'redundant'
                    else:
                        winner = state['bundle'], dict(attempt=attempt, time_ns=now)
                        outcome = 'committed'
        # No state, time, ID or budget is consumed until all checks succeed.
        self._attempts[attempt] = state
        if resource_change:
            self._resources[resource_change[0]] = resource_change[1]
        if winner:
            self._winners[winner[0]] = winner[1]
            if len(self._winners) == len(self._bundles):
                self._complete = now
        self._clock = now
        self._events.append(dict(deepcopy(event), sequence=len(self._events), outcome=outcome))
        return outcome

    def snapshot(self):
        accepted = sorted(e for name in self._winners for e in self._bundles[name]['experts'])
        retired = all(a['retired_ns'] is not None for a in self._attempts.values())
        attempts = {name: dict(a, resources=sorted(a['resources'])) for name, a in self._attempts.items()}
        return deepcopy(dict(schema_version='moe-contribution-state/1', clock_ns=self._clock,
            keys={name: b['key_sha256'] for name, b in self._bundles.items()}, winners=self._winners,
            accepted_experts=accepted, missing_experts=sorted(set(self._spec['expected_experts'])-set(accepted)),
            logical_complete_ns=self._complete, all_started_attempts_retired=retired,
            declared_call_retirable=self._complete is not None and retired,
            attempts=attempts, resources=self._resources, events=self._events,
            execution_scope=('caller_callbacks_simulation_lineage_no_tensor_validation' if
                self._spec['schema_version'] == 'moe-contribution-attempts/2' else
                'untimed_caller_callbacks_no_ASTRA_ns3_or_tensor_validation')))


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def check(config, output):
    """Check a bounded declared transcript. Never execute a provider or timer."""
    config, output = Path(config).resolve(), Path(output).resolve()
    require(config.is_file() and config.stat().st_size <= MAX_INPUT, 'missing or oversized transcript')
    raw = config.read_bytes()
    require(len(raw) <= MAX_INPUT, 'oversized transcript')
    transcript = json.loads(raw, object_pairs_hook=unique)
    fields(transcript, 'schema_version spec events', 'transcript')
    require(transcript['schema_version'] == 'moe-contribution-transcript/1', 'unsupported transcript schema')
    runtime = ContributionAttempts(transcript['spec'])
    require(type(transcript['events']) is list and len(transcript['events']) <= runtime._spec['limits']['max_events'],
            'transcript event budget exceeded')
    for event in transcript['events']:
        runtime.apply(event)
    report = dict(transcript_valid=True, transcript_sha256=hashlib.sha256(raw).hexdigest(), **runtime.snapshot())
    # A valid partial transcript is not task completion, nor a network result.
    output.mkdir(parents=True, exist_ok=False)
    (output/'input.json').write_bytes(raw)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    return report
