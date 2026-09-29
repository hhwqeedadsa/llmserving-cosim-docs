"""Continuous source-indexed decode input on the existing selected-layer service."""
from copy import deepcopy

from .data_inputs import load_inputs
from .plan import build_plan, require
from .service import Service


class DataService(Service):
    def __init__(self, config, layer):
        self.data_config = deepcopy(config)
        legacy, self.routes, self.profile, self.evidence = load_inputs(config, layer)
        self.consumed = set()
        self.memory_history = []
        super().__init__(legacy, layer)

    def make_schedulers(self):
        from serving.core.scheduler import Scheduler
        from serving.core.memory_model import Device
        memory = self.data_config['memory']
        audit = self.evidence['model']
        total = memory['device_bytes']
        reserved = memory['workspace_bytes'] + memory['communication_buffer_bytes']
        schedulers = []
        for rank in self.ranks:
            scheduler = Scheduler(self.config['model'], 0, rank, 1, 1, 1, 1, 1,
                total/2**30, 0, rank, 'decode', 16, memory['block_size'], len(self.config['requests']),
                False, False, None, None, ep_size=4, npu_memory_utilization=(total-reserved)/total)
            scheduler.memory.allocate(reserved, Device.NPU)
            require(scheduler.memory.weight == audit['weight_bytes_per_rank'] and
                    scheduler.memory.get_kv(1) == audit['kv_bytes_per_token_per_rank'] and
                    scheduler.kv.npu_pool.num_blocks == audit['kv_capacity_blocks'],
                    'Native scheduler and independent full-model budget disagree')
            schedulers.append(scheduler)
        return schedulers

    def snapshot(self):
        rows = super().snapshot()
        for row in rows:
            source = self.inputs[row['request_id']]
            row['completed_segment_steps'] = len(row['intertoken_latencies_ns'])
            row['next_route_decode_index'] = source['generated_tokens']-1 + row['completed_segment_steps']
            row['execution_scope'] = 'single_selected_moe_layer'
        return rows

    def make_plan(self, candidate):
        records = []
        for token in candidate['tokens']:
            source = self.inputs[int(token['request_id'])]
            original = token['token_index']
            token['token_index'] = original - source['prompt_tokens']
            key = token['request_id'], token['token_index'], token['layer_id']
            require(key in self.routes and key not in self.consumed, 'Route missing or consumed twice')
            record = self.routes[key]
            require(record['expert_ids'] == token['expert_ids'] and record['source_token_index'] == original,
                    'Scheduler and route source disagree')
            records.append(deepcopy(record))
        require(len({t['src_rank'] for t in candidate['tokens']}) == len(candidate['tokens']),
                'Profile domain requires one local Token per active rank')
        candidate['compute_profile_ns'] = deepcopy(self.profile)
        plan = build_plan(candidate, self.identities)
        plan.update(route_source=self.data_config['routes']['kind']+'_indexed_file',
            compute_source=self.evidence['compute']['kind'], input_provenance=dict(
                model_file=self.data_config['model_file'], route_records=records,
                compute=self.evidence['compute'], execution_scope='single_selected_moe_layer'))
        self.consumed.update((r['request_id'],r['token_index'],r['layer_id']) for r in records)
        return plan

    def result_fields(self):
        return dict(input_evidence='input_evidence.json', route_records_consumed=len(self.consumed),
            route_records_expected=len(self.routes), memory_events=self.memory_history,
            memory_final=self.memory_snapshot())

    def memory_snapshot(self):
        return [dict(rank_id=rank,
                kv_capacity_blocks=s.kv.npu_pool.num_blocks, kv_used_bytes=s.kv.npu_used_bytes(),
                used_bytes=s.memory.npu_used) for rank,s in enumerate(self.schedulers)]

    def decide(self, message, directory):
        response = super().decide(message, directory)
        snapshot = self.memory_snapshot()
        audit = self.evidence['model']
        reserved = audit['weight_bytes_per_rank'] + self.data_config['memory']['workspace_bytes'] + self.data_config['memory']['communication_buffer_bytes']
        require(all(r['used_bytes'] == reserved+r['kv_used_bytes'] <= self.data_config['memory']['device_bytes']
                    for r in snapshot), 'Memory ledger exceeds declared budget')
        self.memory_history.append(dict(time_ns=self.current_ns, ranks=snapshot))
        if response['service_finished']:
            require(self.consumed == set(self.routes), 'Service ended before consuming the route segment')
        return response

    def manifest_fields(self):
        return dict(routing_source=self.data_config['routes']['kind']+'_indexed_file',
            compute_source=self.evidence['compute']['kind'], compute_scope='single_selected_moe_layer',
            executed_layer_ids=self.evidence['model']['executed_layer_ids'],
            latency_scope='selected_layer_iterations_only_not_full_model_ITL_or_TTFT',
            model_source='locked_actual_model_config', full_model_execution_validated=False,
            route_data_is_synthetic=self.data_config['routes']['kind']=='synthetic',
            target_profile_available=False, communication_calibration_status='unvalidated_reference')
