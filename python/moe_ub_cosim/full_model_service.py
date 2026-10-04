"""Full Decode graph adapter; native scheduling and KV lifecycle remain shared."""
from copy import deepcopy

from .composed_service import ComposedService
from .configured_service import validate_configured_inputs
from .full_model import audit_full_capacity, build_full_model, validate_profile
from .model_capacity import fields
from .plan import RunIdentities, require


class FullModelService(ComposedService):
    def validate_inputs(self, config, segments):
        fields(config,'schema_version run_id mode execution model groups requests','full model service')
        require(config['schema_version']=='moe-ub-configured-service/4','Unsupported full model service schema')
        require(isinstance(config['groups'],list) and config['groups'],'Missing full model groups')
        self.full_profiles={};self.full_models={}
        prepared=deepcopy(config);prepared['schema_version']='moe-ub-configured-service/3'
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
            evidence['model'].update(execution_scope='full_model_decode_reference',executed_layer_ids=ids,
                unexecuted_layer_ids=[],unexecuted_components=[],compute_validation='reference_stage_costs_only')
            evidence.update(full_model_profile=deepcopy(self.full_profiles[name]),
                compute_source='full_model_stage_reference_with_context_cost')
        self.evidence['compute_source']='full_model_stage_reference_with_context_cost'
        return result

    def validate_layer(self, config, layer):
        return validate_configured_inputs(config,layer,capacity_auditor=audit_full_capacity)

    def check_segment(self, layers, group):
        name=group['group_id']
        build_full_model(layers,RunIdentities(),[[] for _ in layers],self.full_models[name],self.full_profiles[name])

    def build_iteration(self, configs, provenance):
        name=configs[0]['group_id']
        return build_full_model(configs,self.identities,provenance,self.full_models[name],self.full_profiles[name])

    def make_plan(self, candidate):
        plan=super().make_plan(candidate)
        plan['compute_source']='full_model_stage_reference_with_context_cost'
        plan['input_provenance'].update(execution_scope='full_model_decode_reference',compute_source=plan['compute_source'])
        return plan

    def deployment(self):
        result=super().deployment();result['schema_version']='moe-ub-service-deployment/5'
        for group in result['groups']:
            name=group['group_id']
            group.update(model_spec=deepcopy(self.full_models[name]),full_model_profile=deepcopy(self.full_profiles[name]))
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
        return result


from .evidence_service import EvidenceService


class FullModelEvidenceService(EvidenceService, FullModelService):
    def manifest_fields(self):
        result=super().manifest_fields()
        result.update(compute_scope='full_model_decode_reference',
            latency_scope='observed_full_decode_iterations_from_ready_kv_not_original_TTFT')
        return result
