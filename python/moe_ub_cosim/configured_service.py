"""Declared model capacity and conservative ready-KV decode admission."""
import hashlib
import json
from pathlib import Path
import re
from copy import deepcopy

from .model_capacity import audit_capacity, fields, I64
from .plan import batch_limit, build_plan, integer, require, validate_config
from .service import Service, group_layer


def validate_configured_inputs(config, layer):
    operator_profile = layer.get('operator_profile')
    if operator_profile is not None:
        require(config['model_spec'].get('schema_version') == 'moe-ub-model/1',
                'MLA compute requires explicit reference costs; GQA operator profiles do not cover MLA/quantized weights')
        from .operator_profile import validate_profile
        validate_profile(operator_profile, layer)
        domain = operator_profile['domain']; model = config['model_spec']
        require(domain['intermediate_dim'] == model['layers'][layer['layer_id']]['intermediate_size'] and
                all(domain[k] == model['attention'][k] for k in ('num_heads', 'num_kv_heads', 'head_dim')),
                'Operator profile differs from model structure')
        require(domain['dtype'] == model['dtypes']['weights'],
                'Operator profile differs from model weight dtype')
        # Placeholder and individual route checks are not actual scheduled batches.
        # Validate their routing independently; exact profile coverage is checked
        # when the real selected batch is constructed.
        layer = deepcopy(layer); layer.pop('operator_profile')
    fields(config, 'schema_version run_id mode execution model model_spec memory scheduling groups requests',
           'configured service')
    batched = config['schema_version'] == 'moe-ub-configured-service/2'
    require(config['schema_version'] in ('moe-ub-configured-service/1','moe-ub-configured-service/2') and config['mode'] == 'fixture' and
            config['execution'] == {'entry_mode': 'service'}, 'Unsupported configured service mode/schema')
    require((layer['schema_version'] == 'moe-ub-layer-fixture/3') == batched, 'Service/layer batching schema mismatch')
    require(isinstance(config['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', config['run_id']),
            'Invalid run_id')
    validate_config(layer)
    groups = config['groups']
    require(isinstance(groups, list) and len(groups) == 1, 'Configured service currently executes one EP group')
    group = groups[0]
    fields(group, 'group_id rank_ids rank_to_ub_node expert_placement' +
           (' deployment' if 'deployment' in layer else ''), 'group')
    require(all(group[k] == layer[k] for k in group), 'Service group and selected layer differ')
    require(isinstance(group['group_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', group['group_id']),
            'Invalid group_id')
    validate_config(group_layer(layer, group))
    audit = audit_capacity(config['model_spec'], config['memory'], layer)
    require(config['model'] == config['model_spec']['name'], 'Service/model identity mismatch')
    fields(config['scheduling'], 'max_num_seqs max_num_batched_tokens', 'scheduling')
    maximum = config['model_spec']['max_position_embeddings']
    if batched:
        require(config['scheduling'] == layer['batch_limits'], 'Service/layer batch limits differ')
        batch_limit(layer)
        require(layer['compute_dtypes'] == {k:config['model_spec']['dtypes'][k] for k in layer['compute_dtypes']},
                'Model and batch compute dtypes differ')
    else:
        integer(config['scheduling']['max_num_seqs'], 'max_num_seqs (current single-request boundary)', 1, 1)
        integer(config['scheduling']['max_num_batched_tokens'], 'max_num_batched_tokens', 1, maximum)
    requests = config['requests']
    require(isinstance(requests, list), 'Requests must be a list')
    ids = set()
    by_rank = {r['rank_id']: r for r in audit['ranks']}
    block_size = config['memory']['block_size']
    requirements = []
    for source in requests:
        fields(source, 'request_id rank_id arrival_ns prompt_tokens generated_tokens decode_routes', 'request')
        identity = integer(source['request_id'], 'request_id', 0, I64)
        require(identity not in ids, 'Duplicate request_id')
        ids.add(identity)
        rank = integer(source['rank_id'], 'rank_id', 0, 2**31-2)
        require(rank in by_rank, 'Unknown request rank')
        integer(source['arrival_ns'], 'arrival_ns', 0, I64-1000001)
        integer(source['prompt_tokens'], 'prompt_tokens', 1, maximum)
        integer(source['generated_tokens'], 'generated_tokens', 1, maximum)
        routes = source['decode_routes']
        require(isinstance(routes, list) and routes, 'Require explicit nonempty decode routes')
        target = source['prompt_tokens'] + source['generated_tokens'] + len(routes)
        require(target <= maximum, 'Request exceeds declared model context')
        needed = (target-1+block_size-1)//block_size
        require(needed <= by_rank[rank]['kv_capacity_blocks'],
                f'Request {identity}: final KV demand {needed} blocks exceeds rank {rank} total KV capacity')
        requirements.append(dict(request_id=identity, rank_id=rank, target_tokens=target,
                                 reserved_kv_tokens=target-1, reserved_kv_blocks=needed))
        for step, experts in enumerate(routes):
            candidate = group_layer(layer, group)
            candidate['tokens'] = [dict(request_id=str(identity), src_rank=rank, phase='decode',
                token_index=source['prompt_tokens']+source['generated_tokens']-1+step,
                layer_id=layer['layer_id'], expert_ids=experts)]
            build_plan(candidate)
    digest = hashlib.sha256(json.dumps(config['model_spec'], sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    evidence = dict(model=audit, model_spec_sha256=digest,
                         reservation_policy='final_input_output_minus_one', requests=requirements,
                         scheduler_initialization='root_configured_scheduler_adapter',
                         compute_source=(operator_profile['basis']+'_with_reference_residual'
                                         if operator_profile is not None else 'fixed_reference'),
                         calibration_status='unvalidated_reference')
    return dict(batched=batched, audit_by_rank=by_rank,
                requirements={r['request_id']: r for r in requirements}, evidence=evidence)


class ConfiguredService(Service):
    def validate_inputs(self, config, layer):
        self.__dict__.update(validate_configured_inputs(config, layer))
        self.memory_events = []
        self.batch_events = []
        self.completed_ids = set()

    def make_schedulers(self):
        from .configured_scheduler import ConfiguredScheduler
        return [ConfiguredScheduler(self.config['model_spec'], self.audit_by_rank[r],
                self.config['memory']['block_size'], self.config['scheduling']['max_num_batched_tokens'],
                len(self.config['requests']), self.config['scheduling']['max_num_seqs']) for r in self.ranks]

    def memory_snapshot(self):
        result = []
        for rank, scheduler in self.scheduler_by_rank.items():
            pool = scheduler.kv.npu_pool
            a = self.audit_by_rank[rank]
            require(scheduler.kv is scheduler.memory.kv and pool is scheduler.memory.npu_pool,
                    'Scheduler and memory must share one KV pool')
            require(scheduler.memory.npu_used == a['non_kv_bytes']+pool.used_bytes() <= a['device_bytes'],
                    'Memory ledger exceeds declared rank capacity')
            require(scheduler.num_preemptions == scheduler.recompute_tokens == 0,
                    'Unmodeled preemption or recompute')
            resident = {str(rid): len(blocks) for rid, blocks in scheduler.kv.req_to_blocks.items()}
            require(sum(resident.values()) == pool.used_blocks, 'KV reservation and pool occupancy disagree')
            if self.batched:
                require(set(resident) == {str(r.id) for r in scheduler.running} and not scheduler.waiting,
                        'Resident requests differ from the single KV ledger')
                require(all(len(blocks) == self.requirements[rid]['reserved_kv_blocks']
                            for rid, blocks in scheduler.kv.req_to_blocks.items()), 'Final KV reservation changed')
            result.append(dict(rank_id=rank, weight_bytes=a['weight_bytes'], non_kv_bytes=a['non_kv_bytes'],
                kv_capacity_blocks=pool.num_blocks, free_blocks=pool.get_num_free_blocks(),
                kv_used_bytes=pool.used_bytes(), used_bytes=scheduler.memory.npu_used,
                request_blocks=resident))
        return result

    def admit_pending(self, rank):
        scheduler = self.scheduler_by_rank[rank]
        if self.batched:
            require(not scheduler.inflight, 'Cannot admit during an active rank batch')
            while self.pending[rank]:
                request = self.pending[rank][0]
                needed = self.requirements[request.id]['reserved_kv_blocks']
                reason = 'sequence_budget' if len(scheduler.running) >= scheduler.max_num_seqs else (
                    'kv_capacity' if scheduler.kv.npu_pool.get_num_free_blocks() < needed else None)
                if reason:
                    self.memory_events.append(dict(event='wait', reason=reason, time_ns=self.current_ns,
                        request_id=request.id, ranks=self.memory_snapshot()))
                    require(bool(scheduler.running), 'Idle rank cannot admit a request that fits its total capacity')
                    break  # FCFS admission; no bypass of the waiting head.
                self.reserve_request(rank, request)
            return
        if not scheduler.is_request_empty() or not self.pending[rank]:
            return
        request = self.pending[rank][0]
        required = self.requirements[request.id]
        require(scheduler.kv.npu_pool.get_num_free_blocks() >= required['reserved_kv_blocks'],
                'Idle single-request rank retained KV reservations')
        self.reserve_request(rank, request)

    def reserve_request(self, rank, request):
        scheduler = self.scheduler_by_rank[rank]
        required = self.requirements[request.id]
        before = request.num_computed_tokens, request.num_tokens_reached
        allocated = scheduler.kv.allocate_slots(request, required['reserved_kv_tokens']-request.num_computed_tokens)
        require(allocated is not None, 'Checked final KV reservation failed')
        scheduler.add_decode(request)  # Reuses reserved blocks; does not allocate a second ledger.
        require(before == (request.num_computed_tokens, request.num_tokens_reached),
                'KV reservation changed request progress')
        self.pending[rank].pop(0)
        self.memory_events.append(dict(event='admit', time_ns=self.current_ns, request_id=request.id,
            num_computed_tokens=request.num_computed_tokens, num_tokens_reached=request.num_tokens_reached,
            reserved_kv_tokens=required['reserved_kv_tokens'], ranks=self.memory_snapshot()))

    def validate_batch(self, rank, batch):
        if not self.batched:
            return super().validate_batch(rank, batch)
        require(batch is not None and 1 <= len(batch.requests) <= min(self.scheduler_by_rank[rank].max_num_seqs,
                self.scheduler_by_rank[rank].max_num_batched_tokens), 'Empty or over-budget decode batch')
        ids = [r.id for r in batch.requests]
        require(len(set(ids)) == len(ids) and batch.num_decode == len(ids) and batch.num_prefill == 0 and
                batch.scheduled_tokens == {rid:1 for rid in ids}, 'Invalid per-request scheduled_tokens')
        require(batch.q_list == [1]*len(ids) and batch.k_list == [r.num_computed_tokens-1 for r in batch.requests] and
                all(r.num_computed_tokens == r.num_tokens_reached < r.output for r in batch.requests) and
                batch.load == batch.evict == batch.write_through == batch.pd_kv_send_tokens == 0,
                'Decode batch changed progress or introduced unmodeled work')
        self.batch_events.append(dict(event='scheduled', time_ns=self.current_ns, rank_id=rank,
            batch_id=batch.batch_id, scheduled_tokens=dict(batch.scheduled_tokens),
            request_ids=ids, context_tokens=[k+1 for k in batch.k_list],
            resident_request_ids=[r.id for r in self.scheduler_by_rank[rank].running],
            ranks=self.memory_snapshot()))

    def complete_batch(self, rank, batch, now):
        if not self.batched:
            return super().complete_batch(rank, batch, now)
        scheduler = self.scheduler_by_rank[rank]
        before = {r.id:(r.num_computed_tokens,r.num_tokens_reached,len(r.itl)) for r in self.requests}
        expected = {r.id for r in batch.requests if r.num_tokens_reached+1 == r.output}
        prompt, generated, finished = scheduler.add_done(batch.batch_id+1, rank, now)
        require(prompt == 0 and generated == sum(batch.scheduled_tokens.values()) == len(batch.requests) and
                not scheduler.inflight and {r.id for r in finished} == expected and len(finished) == len(expected),
                'Per-request completion mismatch')
        for request in self.requests:
            computed, reached, count = before[request.id]
            selected = request.id in batch.scheduled_tokens
            require(request.num_computed_tokens == computed and request.num_tokens_reached == reached+int(selected) and
                    len(request.itl) == count+int(selected), 'Unscheduled request advanced or scheduled request lost progress')
        self.record_completion(rank, finished, now)
        self.batch_events.append(dict(event='completed', time_ns=now, rank_id=rank,
            batch_id=batch.batch_id, generated_tokens=generated, request_ids=[r.id for r in batch.requests],
            finished_request_ids=[r.id for r in finished], ranks=self.memory_snapshot()))

    def make_plan(self, candidate):
        plan = super().make_plan(candidate)
        plan['input_provenance'] = dict(model_spec_sha256=self.evidence['model_spec_sha256'],
            executed_layer_ids=self.evidence['model']['executed_layer_ids'], compute_source=self.evidence['compute_source'],
            execution_scope='single_selected_moe_layer', calibration_status='unvalidated_reference')
        return plan

    def record_completion(self, rank, finished_requests, now):
        for request in finished_requests:
            require(request.id not in self.completed_ids, 'Repeated request completion')
            self.completed_ids.add(request.id)
            require(request.id not in self.scheduler_by_rank[rank].kv.req_to_blocks,
                    'Completed request retains reserved KV')
            self.memory_events.append(dict(event='release', time_ns=now, request_id=request.id,
                                            ranks=self.memory_snapshot()))

    def decide(self, message, directory):
        response = super().decide(message, directory)
        snapshot = self.memory_snapshot()
        self.memory_events.append(dict(event='boundary', time_ns=self.current_ns, ranks=snapshot))
        if response['service_finished']:
            require(all(not r['request_blocks'] and r['kv_used_bytes'] == 0 for r in snapshot), 'KV leak at service end')
            self.validate_finished()
        return response

    def validate_finished(self):
        require(self.completed_ids == set(self.inputs), 'Service ended with unfinished request accounting')

    def result_fields(self):
        fields = dict(input_evidence='input_evidence.json', memory_events=self.memory_events,
                      memory_final=self.memory_snapshot())
        if self.batched:
            fields['batch_events'] = self.batch_events
        return fields

    def manifest_fields(self):
        result = dict(model_source=self.config['model_spec']['source']['kind'],
            model_spec_sha256=self.evidence['model_spec_sha256'],
            compute_scope='single_selected_moe_layer', compute_source='fixed_reference',
            executed_layer_ids=self.evidence['model']['executed_layer_ids'],
            latency_scope='selected_layer_iterations_only_not_full_model_ITL_or_TTFT',
            full_model_execution_validated=False, target_deployment_validated=False,
            target_profile_available=False, route_data_is_synthetic=True,
            memory_basis=self.evidence['model']['memory_basis'],
            scheduler_adapter_sha256=hashlib.sha256((Path(__file__).parent /
                                                   'configured_scheduler.py').read_bytes()).hexdigest())
        if self.batched:
            result.update(batching='iteration_boundary_ready_kv_decode',
                          batch_limits=self.config['scheduling'], admission_policy='fcfs_final_kv_reservation')
        if 'operator_profile' in self.layer:
            result.update(compute_source=self.layer['operator_profile']['basis']+'_with_reference_residual',
                          target_profile_available=False)
        if self.evidence['model']['model']['schema_version'] == 'moe-ub-model/2':
            result.update(capacity_model_schema='moe-ub-model/2', kv_layout='absorbed_latent_rope',
                          mla_kernel_validated=False, quantized_kernels_executed=False)
        return result
