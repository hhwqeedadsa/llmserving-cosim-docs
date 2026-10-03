"""Declared model capacity and endpoint mapping on the one-request decode service."""
import hashlib
import json
from pathlib import Path
import re

from .model_capacity import audit_capacity, fields, I64
from .plan import build_plan, integer, require, validate_config
from .service import Service, group_layer


class ConfiguredService(Service):
    def validate_inputs(self, config, layer):
        fields(config, 'schema_version run_id mode execution model model_spec memory scheduling groups requests',
               'configured service')
        require(config['schema_version'] == 'moe-ub-configured-service/1' and config['mode'] == 'fixture' and
                config['execution'] == {'entry_mode': 'service'}, 'Unsupported configured service mode/schema')
        require(isinstance(config['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', config['run_id']),
                'Invalid run_id')
        validate_config(layer)
        groups = config['groups']
        require(isinstance(groups, list) and len(groups) == 1, 'Configured service currently executes one EP group')
        group = groups[0]
        fields(group, 'group_id rank_ids rank_to_ub_node expert_placement', 'group')
        require(all(group[k] == layer[k] for k in group), 'Service group and selected layer differ')
        require(isinstance(group['group_id'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+', group['group_id']),
                'Invalid group_id')
        validate_config(group_layer(layer, group))
        audit = audit_capacity(config['model_spec'], config['memory'], layer)
        require(config['model'] == config['model_spec']['name'], 'Service/model identity mismatch')
        fields(config['scheduling'], 'max_num_seqs max_num_batched_tokens', 'scheduling')
        integer(config['scheduling']['max_num_seqs'], 'max_num_seqs (current single-request boundary)', 1, 1)
        maximum = config['model_spec']['max_position_embeddings']
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
        self.audit_by_rank = by_rank
        self.requirements = {r['request_id']: r for r in requirements}
        digest = hashlib.sha256(json.dumps(config['model_spec'], sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
        self.evidence = dict(model=audit, model_spec_sha256=digest,
                             reservation_policy='final_input_output_minus_one', requests=requirements,
                             scheduler_initialization='root_configured_scheduler_adapter',
                             compute_source='fixed_reference', calibration_status='unvalidated_reference')
        self.memory_events = []

    def make_schedulers(self):
        from .configured_scheduler import ConfiguredScheduler
        return [ConfiguredScheduler(self.config['model_spec'], self.audit_by_rank[r],
                self.config['memory']['block_size'], self.config['scheduling']['max_num_batched_tokens'],
                len(self.config['requests'])) for r in self.ranks]

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
            result.append(dict(rank_id=rank, weight_bytes=a['weight_bytes'], non_kv_bytes=a['non_kv_bytes'],
                kv_capacity_blocks=pool.num_blocks, free_blocks=pool.get_num_free_blocks(),
                kv_used_bytes=pool.used_bytes(), used_bytes=scheduler.memory.npu_used,
                request_blocks=resident))
        return result

    def admit_pending(self, rank):
        scheduler = self.scheduler_by_rank[rank]
        if not scheduler.is_request_empty() or not self.pending[rank]:
            return
        request = self.pending[rank][0]
        required = self.requirements[request.id]
        require(scheduler.kv.npu_pool.get_num_free_blocks() >= required['reserved_kv_blocks'],
                'Idle single-request rank retained KV reservations')
        before = request.num_computed_tokens, request.num_tokens_reached
        allocated = scheduler.kv.allocate_slots(request, request.output-1-request.num_computed_tokens)
        require(allocated is not None, 'Checked final KV reservation failed')
        scheduler.add_decode(request)  # Reuses reserved blocks; does not allocate a second ledger.
        require(before == (request.num_computed_tokens, request.num_tokens_reached),
                'KV reservation changed request progress')
        self.pending[rank].pop(0)
        self.memory_events.append(dict(event='admit', time_ns=self.current_ns, request_id=request.id,
            num_computed_tokens=request.num_computed_tokens, num_tokens_reached=request.num_tokens_reached,
            reserved_kv_tokens=required['reserved_kv_tokens'], ranks=self.memory_snapshot()))

    def make_plan(self, candidate):
        plan = super().make_plan(candidate)
        plan['input_provenance'] = dict(model_spec_sha256=self.evidence['model_spec_sha256'],
            executed_layer_ids=self.evidence['model']['executed_layer_ids'], compute_source='fixed_reference',
            execution_scope='single_selected_moe_layer', calibration_status='unvalidated_reference')
        return plan

    def record_completion(self, rank, finished_requests, now):
        for request in finished_requests:
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
        return response

    def result_fields(self):
        return dict(input_evidence='input_evidence.json', memory_events=self.memory_events,
                    memory_final=self.memory_snapshot())

    def manifest_fields(self):
        return dict(model_source=self.config['model_spec']['source']['kind'],
            model_spec_sha256=self.evidence['model_spec_sha256'],
            compute_scope='single_selected_moe_layer', compute_source='fixed_reference',
            executed_layer_ids=self.evidence['model']['executed_layer_ids'],
            latency_scope='selected_layer_iterations_only_not_full_model_ITL_or_TTFT',
            full_model_execution_validated=False, target_deployment_validated=False,
            target_profile_available=False, route_data_is_synthetic=True,
            memory_basis=self.evidence['model']['memory_basis'],
            scheduler_adapter_sha256=hashlib.sha256((Path(__file__).parent /
                                                   'configured_scheduler.py').read_bytes()).hexdigest())
