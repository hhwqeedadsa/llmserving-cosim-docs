"""Source-indexed observation fragments on the composed native Scheduler path."""
from copy import deepcopy
from enum import Enum

from .composed_service import ComposedService
from .model_capacity import fields
from .plan import require
from .route_evidence import read_json, select_routes


class ObservationStatus(Enum):
    OBSERVATION_ENDED = 1


class EvidenceService(ComposedService):
    def validate_inputs(self, config, segments):
        fields(config,'schema_version run_id service route_manifest requests compute_profiles','evidence service')
        require(config['schema_version']=='moe-ub-evidence-service/1','Unsupported evidence service')
        self.observation_config=deepcopy(config)
        service=deepcopy(config['service']);layers=deepcopy(segments)
        require(service['schema_version'] in ('moe-ub-configured-service/3','moe-ub-configured-service/4') and service['run_id']==config['run_id'] and
                service['requests']==[],'Evidence service needs an empty composed-service template')
        require(isinstance(config['requests'],list) and config['requests'],'Require observed requests')
        selected=[layer['layer_id'] for layer in layers['groups'][0]['layers']]
        normalized,self.route_records,self.route_evidence=select_routes(config['route_manifest'],config['requests'],
            [g['model_spec'] for g in service['groups']],selected)
        service['requests']=normalized
        self.window_requests={r['request_id']:r for r in config['requests']}
        require(len(self.window_requests)==len(config['requests']),'Duplicate observed request')
        self.retired_ids=set();self.observation_events=[]
        require(isinstance(config['compute_profiles'],list),'Invalid compute profile references')
        profile_evidence=[];seen=set()
        for entry in config['compute_profiles']:
            fields(entry,'group_id layer_id file','compute reference')
            key=entry['group_id'],entry['layer_id'];require(key not in seen,'Duplicate layer profile');seen.add(key)
            matching=[l for g in layers['groups'] if g['group_id']==entry['group_id'] for l in g['layers'] if l['layer_id']==entry['layer_id']]
            require(len(matching)==1,'Unknown profile group/layer')
            _,profile=read_json(entry['file']);matching[0]['operator_profile']=profile
            profile_evidence.append(dict(**deepcopy(entry),basis=profile['basis'],domain=profile['domain'],
                device=profile['device'],software=profile['software'],measurement=profile['measurement']))
        prepared=super().validate_inputs(service,layers)
        self.evidence.update(routes=self.route_evidence,operator_profiles=profile_evidence,
            reservation_policy='declared_observation_input_bound_not_request_target',
            request_completion_policy='known_target_only_never_infer_EOS_from_trace_end')
        for evidence in self.evidence['groups'].values():
            evidence['reservation_policy']='declared_observation_input_bound_not_request_target'
            for row in evidence['requests']:
                row['observation_tokens_reached']=row.pop('target_tokens')
                row['target_total_tokens']=self.window_requests[row['request_id']]['target_total_tokens']
        return prepared

    def request_target(self, source):
        target=self.window_requests[source['request_id']]['target_total_tokens']
        # The native scheduler only compares progress against output. Infinity
        # represents an unknown target; it is never exported as a finite length.
        return float('inf') if target is None else target

    def snapshot(self):
        rows=super().snapshot()
        for row in rows:
            source=self.window_requests[row['request_id']]
            row.update(target_tokens=source['target_total_tokens'],
                observed_steps_completed=len(row['intertoken_latencies_ns']),observed_steps_requested=source['decode_steps'],
                completion_reason=('observation_window_end' if row['request_id'] in self.retired_ids else
                    'declared_target_reached' if row['status']=='FINISHED' else None),eos_known=False)
        return rows

    def complete_batch(self, rank, batch, now):
        super().complete_batch(rank,batch,now)
        scheduler=self.scheduler_by_rank[rank]
        for request in batch.requests:
            source=self.window_requests[request.id]
            require(len(request.itl)<=source['decode_steps'],'Observed past declared fragment')
            if len(request.itl)!=source['decode_steps']:continue
            self.observation_events.append(dict(event='observation_end',time_ns=now,request_id=request.id,
                completed_kv_tokens=request.num_computed_tokens,tokens_reached=request.num_tokens_reached,
                declared_target_total_tokens=source['target_total_tokens'],
                request_completed=request.id in self.completed_ids,stop_reason='observation_window_end'))
            if request.id in self.completed_ids:continue
            require(request in scheduler.running and request not in scheduler.done and request.id not in self.retired_ids,
                    'Invalid observation retirement')
            scheduler.kv.free(request);scheduler.running.remove(request)
            request.status=ObservationStatus.OBSERVATION_ENDED;self.retired_ids.add(request.id)
            self.memory_events.append(dict(event='observation_cleanup',time_ns=now,request_id=request.id,
                after_observation=True,ranks=self.memory_snapshot()))

    def validate_finished(self):
        require(not self.retired_ids.intersection(self.completed_ids) and
                self.retired_ids|self.completed_ids==set(self.inputs),'Unaccounted observed requests')
        require(all(len(r.itl)==self.window_requests[r.id]['decode_steps'] for r in self.requests),
                'Observation ended before its actual iterations completed')

    def make_plan(self,candidate):
        plan=super().make_plan(candidate);records=[]
        for child in plan['layers']:
            selected=[]
            for token in child['input']['tokens']:
                key=token['request_id'],token['token_index'],token['layer_id']
                record=self.route_records[key]
                require(record['expert_ids']==token['expert_ids'],'Imported route changed during execution')
                selected.append(deepcopy(record))
            child['input_provenance']['route_records']=selected;records.extend(selected)
            child['route_source']=self.route_evidence['source']['kind']
        plan['input_provenance'].update(route_records=records,route_manifest=deepcopy(self.observation_config['route_manifest']))
        plan['route_source']=self.route_evidence['source']['kind']
        return plan

    def result_fields(self):
        return dict(super().result_fields(),observation_events=self.observation_events,
            requests_completed=sorted(self.completed_ids),requests_censored=sorted(self.retired_ids),
            all_requests_completed=len(self.completed_ids)==len(self.inputs),
            service_finished_semantics='declared_observation_workload_finished',
            observation_finished=len(self.completed_ids|self.retired_ids)==len(self.inputs))

    def manifest_fields(self):
        return dict(super().manifest_fields(),routing_source=self.route_evidence['source']['kind'],
            route_data_is_synthetic=self.route_evidence['route_data_is_synthetic'],
            route_manifest=deepcopy(self.observation_config['route_manifest']),
            latency_scope='observed_layer_segment_iterations_not_request_ITL_or_TTFT',
            stop_policy='per_request_contiguous_fragment_after_real_completion',
            admission_policy='fcfs_observation_input_bound_reservation',
            eos_inferred=False,compute_source=self.evidence['compute_source'])
