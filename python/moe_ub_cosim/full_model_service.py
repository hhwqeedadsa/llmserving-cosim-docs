"""Full Decode graph adapter; native scheduling and KV lifecycle remain shared."""
from copy import deepcopy

from .composed_service import ComposedService
from .configured_service import validate_configured_inputs
from .full_model import audit_full_capacity, build_full_model, validate_profile
from .model_capacity import fields
from .plan import RunIdentities, require


class FullModelService(ComposedService):
    def __init__(self,config,segments,*,graph_storage=None):
        self.graph_storage=graph_storage
        super().__init__(config,segments)

    def validate_inputs(self, config, segments):
        fields(config,'schema_version run_id mode execution model groups requests','full model service')
        self.segment=config['execution'].get('segment')
        require(config['schema_version']==('moe-ub-configured-service/5' if self.segment is not None else 'moe-ub-configured-service/4'),
                'Unsupported full model/segment service schema')
        self.segment_requests=deepcopy(config['requests']) if self.segment is not None else []
        if self.segment is not None:
            from .segment import validate_segment
            for group in config['groups']:validate_segment(self.segment,group['model_spec'])
            expected={r['request_id']:r for r in self.segment['requests']}
            require(set(expected)=={str(r['request_id']) for r in config['requests']},'Segment initial request set mismatch')
            for request in config['requests']:
                row=expected[str(request['request_id'])]
                require(request['arrival_ns']==0 and len(request['decode_routes'])==1 and
                    request['rank_id']==row['rank_id'] and request['prompt_tokens']==row['prompt_tokens'] and
                    request['generated_tokens']==row['generated_tokens_at_trace_start']+self.segment['decode_step'],
                    'Segment service differs from declared initial requests')
        require(isinstance(config['groups'],list) and config['groups'],'Missing full model groups')
        self.full_profiles={};self.full_models={}
        self.resource_budget=config['execution'].get('resource_budget')
        self.resource_usage=dict(graphs=0,transfers=0,nodes=0)
        if self.resource_budget is not None:
            from .resources import validate_budget
            validate_budget(self.resource_budget)
            fields(config['execution'],'entry_mode resource_budget'+(' segment' if self.segment is not None else ''),'budgeted execution')
            segments=deepcopy(segments)
            for group in segments['groups']:
                for layer in group['layers']:
                    require('resource_budget' not in layer or layer['resource_budget']==self.resource_budget,'Layer/resource budget mismatch')
                    layer['resource_budget']=deepcopy(self.resource_budget)
        else:
            require(all('resource_budget' not in layer for group in segments['groups'] for layer in group['layers']),
                    'Service layer resource budgets require execution.resource_budget')
        prepared={k:deepcopy(v) for k,v in config.items() if k!='requests'}
        prepared['requests']=config['requests']  # Parent validates then owns its required copies.
        prepared['schema_version']='moe-ub-configured-service/3'
        prepared['execution'].pop('segment',None)
        if self.resource_budget is not None:prepared['execution'].pop('resource_budget')
        for group in prepared['groups']:
            fields(group,'group_id model_spec memory scheduling full_model_profile','full model group')
            name=group['group_id'];profile=group.pop('full_model_profile')
            require(isinstance(name,str) and name not in self.full_profiles,'Duplicate full model group')
            require(group['model_spec'].get('schema_version')=='moe-ub-model/2','Full model requires MLA model/2')
            validate_profile(group['model_spec'],profile)
            self.full_profiles[name]=profile;self.full_models[name]=deepcopy(group['model_spec'])
        result=super().validate_inputs(prepared,segments)
        for name,evidence in self.evidence['groups'].items():
            ids=[row['layer_id'] for row in self.full_models[name]['layers']]
            executed=ids if self.segment is None else list(range(self.segment['first_layer'],self.segment['first_layer']+self.segment['layer_count']))
            evidence['model'].update(execution_scope='full_model_decode_reference' if self.segment is None else 'finite_moe_segment_reference',
                executed_layer_ids=executed,unexecuted_layer_ids=[i for i in ids if i not in executed],
                unexecuted_components=[] if self.segment is None else ['embedding','final_norm','lm_head','sampling'],
                compute_validation='reference_stage_costs_only',capacity_scope='full_declared_model')
            evidence.update(full_model_profile=deepcopy(self.full_profiles[name]),
                compute_source='full_model_stage_reference_with_context_cost')
        self.evidence['compute_source']='full_model_stage_reference_with_context_cost'
        return result

    def validate_layer(self, config, layer):
        return validate_configured_inputs(config,layer,capacity_auditor=audit_full_capacity)

    def check_segment(self, layers, group):
        name=group['group_id']
        if self.segment is not None:
            require(self.graph_storage is None,'Segment preparation does not accept legacy full-round graph-cache policies')
            layers=deepcopy(layers)
            for layer in layers:
                layer['tokens']=[dict(request_id=str(r['request_id']),src_rank=r['rank_id'],phase='decode',
                    token_index=r['prompt_tokens']+r['generated_tokens']-1,layer_id=layer['layer_id'],
                    expert_ids=r['decode_routes'][0][str(layer['layer_id'])]) for r in self.segment_requests if r['rank_id'] in layer['rank_ids']]
        if self.graph_storage is not None:
            self.graph_storage.check_composition(layers,self.full_models[name],self.full_profiles[name])
            return
        build_full_model(layers,RunIdentities(),[[] for _ in layers],self.full_models[name],self.full_profiles[name],segment=self.segment)

    def build_iteration(self, configs, provenance):
        name=configs[0]['group_id']
        if self.resource_budget is not None:
            from .resources import full_usage, check_run
            next_usage=check_run(self.resource_usage,full_usage(configs,self.full_models[name],segment=self.segment),self.resource_budget)
        plan=build_full_model(configs,self.identities,provenance,self.full_models[name],self.full_profiles[name],segment=self.segment)
        if self.resource_budget is not None:self.resource_usage=next_usage
        return plan

    def make_plan(self, candidate):
        plan=super().make_plan(candidate)
        plan['compute_source']='full_model_stage_reference_with_context_cost'
        plan['input_provenance'].update(execution_scope='full_model_decode_reference',compute_source=plan['compute_source'])
        if self.segment is not None:plan['input_provenance']['execution_scope']='finite_moe_segment_reference'
        return plan

    def deployment(self):
        result=super().deployment();result['schema_version']='moe-ub-service-deployment/5' if self.segment is None else 'moe-ub-service-deployment/6'
        for group in result['groups']:
            name=group['group_id']
            group.update(model_spec=deepcopy(self.full_models[name]),full_model_profile=deepcopy(self.full_profiles[name]))
            if self.segment is not None:group['segment']=deepcopy(self.segment)
        return result

    def result_fields(self):
        result=super().result_fields()
        if self.resource_budget is not None:result['graph_resources']=dict(budget=self.resource_budget,used=self.resource_usage)
        if self.segment is not None:
            result.update(completion_scope='selected_segment_only',request_progress_semantics='harness_iteration_not_generated_token',
                segment=deepcopy(self.segment))
        return result

    def manifest_fields(self):
        result=super().manifest_fields()
        result.update(compute_scope='full_model_decode_reference',
            compute_source='full_model_stage_reference_with_context_cost',
            latency_scope='modeled_full_decode_iteration_from_ready_kv_not_original_TTFT',
            full_model_graph_coverage='all_declared_blocks_and_head',
            full_model_graph_execution_validated=False,
            full_model_execution_validated=False, numerical_model_executed=False,
            sampling_basis='reference_cost_only_request_target_or_observation_controls_stop')
        if self.resource_budget is not None:result['graph_resource_budget']=deepcopy(self.resource_budget)
        if self.segment is not None:
            result.update(compute_scope='finite_moe_segment_reference',latency_scope='declared_block_entry_to_segment_completion',
                full_model_graph_coverage='declared_consecutive_moe_blocks',segment=deepcopy(self.segment),
                sampling_basis='not_executed_segment_completion_only')
        return result


from .evidence_service import EvidenceService


class FullModelEvidenceService(EvidenceService, FullModelService):
    def manifest_fields(self):
        result=super().manifest_fields()
        result.update(compute_scope='full_model_decode_reference',
            latency_scope='observed_full_decode_iterations_from_ready_kv_not_original_TTFT')
        return result
