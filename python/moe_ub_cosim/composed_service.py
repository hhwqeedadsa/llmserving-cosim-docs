"""Configured batches across layer segments and independent EP groups."""
from copy import deepcopy
import hashlib
from pathlib import Path

from .configured_service import ConfiguredService, validate_configured_inputs
from .model_capacity import fields
from .multilayer import MAX_SEGMENT_LAYERS, build_multilayer
from .plan import RunIdentities, integer, require
from .route_records import RouteDomain, RouteUsage


def static_layer(layer):
    return {k:deepcopy(v) for k,v in layer.items() if k not in ('graph_id','wave_id','ready_ns','tokens')}


def model_structure(model):
    result = deepcopy(model)
    for layer in result['layers']:
        layer.pop('expert_placement', None)
    return result


class ComposedService(ConfiguredService):
    def validate_layer(self, config, layer):
        return validate_configured_inputs(config, layer)

    def check_segment(self, layers, group):
        build_multilayer(layers,RunIdentities(),[[] for _ in layers])

    def build_iteration(self, configs, provenance):
        return build_multilayer(configs,self.identities,provenance)

    def validate_inputs(self, config, segments):
        fields(config, 'schema_version run_id mode execution model groups requests', 'composed service')
        require(config['schema_version']=='moe-ub-configured-service/3' and config['mode']=='fixture' and
                config['execution']=={'entry_mode':'service'}, 'Unsupported composed service schema/mode')
        fields(segments, 'schema_version groups', 'layer segments')
        require(segments['schema_version']=='moe-ub-layer-segments/1', 'Unsupported layer segments schema')
        require(isinstance(config['groups'],list) and 1 <= len(config['groups']) <= 64 and
                isinstance(segments['groups'],list) and len(segments['groups'])==len(config['groups']),
                'Require 1..64 matching group declarations')
        names = []
        for group in config['groups']:
            fields(group, 'group_id model_spec memory scheduling', 'configured group')
            require(isinstance(group['group_id'],str), 'Invalid group name')
            names.append(group['group_id'])
        require(len(set(names))==len(names), 'Duplicate group name')
        templates = {}
        for entry in segments['groups']:
            fields(entry,'group_id layers','group segment')
            name=entry['group_id']
            require(isinstance(name,str) and name in names and name not in templates, 'Unknown/duplicate segment group')
            layers=entry['layers']
            require(isinstance(layers,list) and 1 <= len(layers) <= MAX_SEGMENT_LAYERS,
                    'Layer segment exceeds host layer budget')
            require(all(isinstance(l,dict) and l.get('schema_version')=='moe-ub-layer-fixture/3' and
                        l.get('group_id')==name for l in layers), 'Segment requires matching layer/3 group')
            templates[name]=deepcopy(layers)
        require(isinstance(config['requests'],list), 'Requests must be a list')
        ids=set()
        for request in config['requests']:
            fields(request,'request_id rank_id arrival_ns prompt_tokens generated_tokens decode_routes','request')
            identity=integer(request['request_id'],'request_id')
            require(identity not in ids, 'Duplicate request_id');ids.add(identity)
            integer(request['rank_id'],'request rank',0,2**31-2)
            require(isinstance(request['decode_routes'],list) and request['decode_routes'], 'Missing decode routes')

        normalized={k:deepcopy(v) for k,v in config.items() if k not in ('groups','requests')}
        normalized['groups']=[];normalized['requests']=[]
        self.audit_by_rank, self.requirements, self.policies = {}, {}, {}
        self.evidence=dict(groups={}, reservation_policy='final_input_output_minus_one',
            compute_source='fixed_reference', calibration_status='unvalidated_reference')
        ranks,nodes,dies,chips=set(),set(),set(),{}
        structure=selected=slots=physical=None
        for group in config['groups']:
            name=group['group_id'];layers=templates[name];first=layers[0]
            group_ranks=first['rank_ids'];group_nodes=first['rank_to_ub_node']
            require(not ranks.intersection(group_ranks) and not nodes.intersection(group_nodes),
                    'Overlapping rank or UB endpoint across groups')
            ranks.update(group_ranks);nodes.update(group_nodes)
            requests=[r for r in config['requests'] if r['rank_id'] in group_ranks]
            layer_ids=[l['layer_id'] for l in layers]
            require(selected is None or selected==layer_ids, 'Groups must execute the same layer segment')
            selected=layer_ids
            for request in requests:
                require(all(isinstance(step,dict) and set(step)=={str(i) for i in layer_ids}
                            for step in request['decode_routes']), 'Incomplete or extra per-layer route')
            audits=[];first_state=None
            for layer in layers:
                require(layer['rank_ids']==group_ranks and layer['rank_to_ub_node']==group_nodes,
                        'Layer membership/mapping changed within segment')
                local_requests=[dict({k:deepcopy(v) for k,v in request.items() if k!='decode_routes'},
                    decode_routes=[deepcopy(step[str(layer['layer_id'])]) for step in request['decode_routes']])
                    for request in requests]
                declaration={k:deepcopy(layer[k]) for k in ('group_id','rank_ids','rank_to_ub_node','expert_placement')}
                if 'deployment' in layer:declaration['deployment']=deepcopy(layer['deployment'])
                single=dict(schema_version='moe-ub-configured-service/2', run_id=config['run_id'],
                    mode=config['mode'], execution=config['execution'], model=config['model'],
                    model_spec=group['model_spec'], memory=group['memory'], scheduling=group['scheduling'],
                    groups=[declaration], requests=local_requests)
                state=self.validate_layer(single,layer)
                audits.append(state['evidence']['model'])
                if first_state is None:
                    first_state=state
                    normalized['groups'].append(declaration)
                    normalized['requests'].extend(local_requests)
            # Validate full composition, including placeholder Token continuity,
            # before creating schedulers or starting any backend process.
            routing_layers=deepcopy(layers)
            for layer in routing_layers:layer.pop('operator_profile',None)
            self.check_segment(routing_layers,group)
            signature=model_structure(group['model_spec'])
            require(structure is None or structure==signature, 'Heterogeneous model structures are not implemented')
            structure=signature
            require(slots is None or slots==first['send_slots_per_rank'], 'Groups must share the run send-slot setting')
            slots=first['send_slots_per_rank']
            locality='deployment' in first
            require(physical is None or physical==locality, 'Groups must declare physical ownership consistently')
            physical=locality
            if locality:
                for entry in first['deployment']['ranks']:
                    require(entry['die_id'] not in dies, 'Repeated global die across groups');dies.add(entry['die_id'])
                    require(chips.setdefault(entry['chip_id'],entry['server_id'])==entry['server_id'],
                            'Global chip spans multiple servers')
            audit=first_state['evidence']['model']
            # Ranks can reach different layers concurrently: retain every layer's
            # conservative communication budget, not just the largest one.
            bounds=[a['communication_buffer_minimum_bytes'] for a in audits]
            require(all(r['communication_buffer_bytes']>=sum(bounds) for r in audit['ranks']),
                    'Communication buffer below complete layer-segment bound')
            audit.update(execution_scope='consecutive_moe_layer_segment',executed_layer_ids=layer_ids,
                unexecuted_layer_ids=[l['layer_id'] for l in group['model_spec']['layers'] if l['layer_id'] not in layer_ids],
                communication_buffer_minimum_bytes=sum(bounds),layer_communication_buffer_bounds=bounds)
            self.evidence['groups'][name]=first_state['evidence']
            profiled_layers=[l['layer_id'] for l in layers if 'operator_profile' in l]
            if profiled_layers:
                self.evidence['groups'][name].update(compute_source='operator_profiles_with_reference_residual',
                                                    profiled_layer_ids=profiled_layers)
                self.evidence['compute_source']='operator_profiles_with_reference_residual'
            self.audit_by_rank.update(first_state['audit_by_rank'])
            self.requirements.update(first_state['requirements'])
            for rank in group_ranks:self.policies[rank]=deepcopy(group)
        require(all(r['rank_id'] in ranks for r in config['requests']), 'Request outside configured groups')
        self.templates=templates
        self.segment_inputs={r['request_id']:deepcopy(r) for r in config['requests']}
        self.expected_routes=RouteDomain(config['requests'],selected)
        self.consumed=RouteUsage(self.expected_routes)
        self.batched=True;self.memory_events=[];self.batch_events=[];self.completed_ids=set()
        return normalized,deepcopy(templates[names[0]][0])

    def make_schedulers(self):
        from .configured_scheduler import ConfiguredScheduler
        return [ConfiguredScheduler(self.policies[r]['model_spec'],self.audit_by_rank[r],
            self.policies[r]['memory']['block_size'],self.policies[r]['scheduling']['max_num_batched_tokens'],
            len(self.config['requests']),self.policies[r]['scheduling']['max_num_seqs']) for r in self.ranks]

    def iteration_inputs(self, candidate):
        configs,provenance,consumed=[],[],set()
        for template in self.templates[candidate['group_id']]:
            layer=deepcopy(template)
            layer.update(graph_id=candidate['graph_id'],wave_id=candidate['wave_id'],ready_ns=candidate['ready_ns'],tokens=[])
            records=[]
            for original in candidate['tokens']:
                token=deepcopy(original);source=self.segment_inputs[int(token['request_id'])]
                offset=token['token_index']-(source['prompt_tokens']+source['generated_tokens']-1)
                token.update(layer_id=layer['layer_id'],expert_ids=source['decode_routes'][offset][str(layer['layer_id'])])
                key=token['request_id'],token['token_index'],token['layer_id']
                require(key in self.expected_routes and key not in self.consumed and key not in consumed,
                        'Segment route missing or consumed twice')
                consumed.add(key);layer['tokens'].append(token)
                records.append(dict(request_id=token['request_id'],token_index=token['token_index'],
                    layer_id=token['layer_id'],expert_ids=deepcopy(token['expert_ids']),source_decode_index=offset))
            configs.append(layer);provenance.append(records)
        return configs,provenance,consumed

    def iteration_provenance(self, candidate, configs, provenance):
        result=dict(group_id=candidate['group_id'],
            model_spec_sha256=self.evidence['groups'][candidate['group_id']]['model_spec_sha256'],
            route_records=[record for records in provenance for record in records],
            execution_scope='consecutive_moe_layer_segment',compute_source='fixed_reference',
            calibration_status='unvalidated_reference')
        if any('operator_profile' in c for c in configs):
            result['compute_source']='operator_profiles_with_reference_residual'
        return result

    def make_plan(self, candidate):
        configs,provenance,consumed=self.iteration_inputs(candidate)
        plan=self.build_iteration(configs,provenance)
        plan['input_provenance']=self.iteration_provenance(candidate,configs,provenance)
        if any('operator_profile' in c for c in configs):
            plan['compute_source']='operator_profiles_with_reference_residual'
        self.consumed.update(consumed)
        return plan

    def deployment(self):
        groups=deepcopy(self.groups)
        for group in groups:
            layers=self.templates[group['group_id']]
            group.update(batch_limits=deepcopy(layers[0]['batch_limits']),layer_configs=[static_layer(l) for l in layers])
        return dict(schema_version='moe-ub-service-deployment/4',groups=groups)

    def decide(self,message,directory):
        response=super().decide(message,directory)
        if response['service_finished']:
            require(self.consumed==self.expected_routes, 'Service ended before consuming all layer routes')
        return response

    def result_fields(self):
        return dict(super().result_fields(),route_records_consumed=len(self.consumed),
                    route_records_expected=len(self.expected_routes))

    def manifest_fields(self):
        result = dict(compute_scope='consecutive_moe_layer_segment',compute_source=(
            'operator_profiles_with_reference_residual' if any('operator_profile' in l for ls in self.templates.values() for l in ls)
            else 'fixed_reference'),
            groups={name:dict(executed_layer_ids=e['model']['executed_layer_ids'],model_spec_sha256=e['model_spec_sha256'])
                    for name,e in self.evidence['groups'].items()},
            latency_scope='layer_segment_iterations_not_full_model_ITL_or_TTFT',
            full_model_execution_validated=False,target_deployment_validated=False,target_profile_available=False,
            route_data_is_synthetic=True,batching='iteration_boundary_ready_kv_decode',
            admission_policy='fcfs_final_kv_reservation',
            adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        if any(e['model']['model']['schema_version'] == 'moe-ub-model/2' for e in self.evidence['groups'].values()):
            result.update(capacity_model_schema='moe-ub-model/2', kv_layout='absorbed_latent_rope',
                          mla_kernel_validated=False, quantized_kernels_executed=False)
        return result
