"""Capture policy and exact resampling capabilities, separate from simulation."""
from copy import deepcopy

from .plan import integer,require

CATEGORIES=('business_data','attached_metadata','dispatch_count','combine_flag','load_ready')
POLICY_FIELDS={'schema_version','mode','detail_ranges','truth_categories','max_detail_events_per_port','max_window_attribution_entries'}


def metadata_category(transfer):
    role=transfer.get('message_role','data')
    if role=='ready':return 'load_ready'
    if role=='status':
        require(transfer['kind'] in ('dispatch','combine'),'Unknown application status message')
        return 'dispatch_count' if transfer['kind']=='dispatch' else 'combine_flag'
    require(role=='data','Unknown application message role')
    return 'attached_metadata'


def validate_recording(policy,max_ns,*,ports=None,provenance=True):
    require(isinstance(policy,dict) and set(policy)==POLICY_FIELDS and
            policy['schema_version']=='moe-ub-observation-recording/1' and policy['mode'] in ('aggregate','detailed'),
            'Invalid observation recording policy')
    for k in ('max_detail_events_per_port','max_window_attribution_entries'):integer(policy[k],k,1,2**63-1)
    categories=policy['truth_categories']
    require(isinstance(categories,list) and all(type(k) is str and k in CATEGORIES for k in categories) and
            len(categories)==len(set(categories)) and (provenance or not categories),'Invalid retained application message categories')
    require(isinstance(policy['detail_ranges'],list),'Invalid detail ranges')
    require(policy['mode']=='aggregate' or not policy['detail_ranges'],'Detailed mode already captures all selected ports throughout execution')
    known=None if ports is None else {(p['switch_id'],p['port_id']) for p in ports};seen=set()
    for r in policy['detail_ranges']:
        require(set(r)=={'switch_id','port_id','start_ns','end_ns'},'Invalid detail range fields')
        for k in ('switch_id','port_id'):integer(r[k],k,0,2**32-1)
        integer(r['start_ns'],'detail start',0,max_ns-1);integer(r['end_ns'],'detail end',r['start_ns']+1,max_ns)
        key=r['switch_id'],r['port_id']
        require(key not in seen and (known is None or key in known),'Duplicate or unobserved detail port');seen.add(key)
    return policy


def detail_range(policy,port,max_ns):
    if policy['mode']=='detailed':return dict(start_ns=0,end_ns=max_ns)
    for r in policy['detail_ranges']:
        if (r['switch_id'],r['port_id'])==(port['switch_id'],port['port_id']):return {k:r[k] for k in ('start_ns','end_ns')}
    return None


def capabilities(observation,ranks,max_ns):
    from .observation import validate_observation
    clocks,ports=validate_observation(observation,ranks,max_ns)
    policy=observation.get('recording');statistics='statistics' in observation
    if policy:validate_recording(policy,max_ns,ports=ports,provenance=observation['record_provenance'])
    rows=[]
    for port in ports:
        detail=detail_range(policy,port,max_ns) if policy else dict(start_ns=0,end_ns=max_ns)
        rows.append(dict(**port,clock=clocks['switch',port['switch_id']],details=detail,
                         packet_starts=detail is not None,queue_states=statistics and detail is not None))
    result=dict(schema_version='moe-ub-capture-capabilities/1',
        storage='aggregate_with_selected_details' if policy and policy['mode']=='aggregate' else 'detailed',
        port_window_aggregates=bool(policy),queue_statistics=statistics,operation_stages=statistics,
        truth_categories=policy['truth_categories'] if policy else ['business_data'] if observation['record_provenance'] else [],
        ports=rows,aggregate_resampling='aligned_window_merging_only',
        detail_resampling='packet_boundaries_and_clocks; queue_resampling_requires_captured_states')

    if 'send_state' in observation:
        result.update(schema_version='moe-ub-capture-capabilities/2',endpoint_send_state=dict(
            policy=observation['send_state'],coverage_start_ns=0,coverage_end_ns=max_ns,
            resampling='rank_clock_transform_preserves_all_state_updates'))
    if 'native_queue' in observation:
        result.update(schema_version='moe-ub-capture-capabilities/3',native_queue_truth=dict(
            policy=observation['native_queue'],access_scope='internal_truth_not_observed',
            resampling='preserves_original_global_time_and_full_capture_policy'))
        if 'max_request_records' in observation['native_queue']:
            result['native_queue_truth']['request_audit']='all_native_STORE_requests_entire_execution'
    return result


def aligned(source,target,source_clock,target_clock):
    s,e,p=(source[k] for k in ('start_local_ns','end_local_ns','period_ns'))
    a,b,q=(target[k] for k in ('start_local_ns','end_local_ns','period_ns'))
    return source_clock==target_clock and s<=a<b<=e and (a-s)%p==0 and (b==e or (b-s)%p==0) and q%p==0


def first_global(local,clock):
    numerator=(local-clock['offset_ns'])*1000000000;denominator=1000000000+clock['drift_ppb']
    return -(-numerator//denominator)


def sampling_plan(source,target,ranks,max_ns):
    """Run before creating output or materializing an archive. Never interpolate."""
    from .observation import validate_observation
    source_clocks,available=validate_observation(source,ranks,max_ns)
    target_clocks,selected=validate_observation(target,ranks,max_ns)
    require(target.get('resource_budget')==source.get('resource_budget'),'Sampling keeps the source capture resource budget')
    require(target['record_provenance']==source['record_provenance'],'Cannot change capture provenance')
    require(('statistics' in target)==('statistics' in source),'Requested statistics were not captured or differ from source; rerun simulation')
    require(target.get('recording')==source.get('recording'),'Sampling preserves the capture policy and retained message categories')
    require(target.get('send_state')==source.get('send_state'),'Send-state observations were not captured or policy differs; rerun simulation')
    require(target.get('native_queue')==source.get('native_queue'),'Native queue truth was not captured or policy differs; rerun simulation')
    by_port={(p['switch_id'],p['port_id']):p for p in available};result={}
    for port in selected:
        key=port['switch_id'],port['port_id'];require(key in by_port,'Requested port was not captured; rerun simulation')
        old=by_port[key];clock=target_clocks['switch',key[0]]
        policy=source.get('recording')
        if policy and aligned(old,port,source_clocks['switch',key[0]],clock):result[key]='aggregate';continue
        detail=detail_range(policy,old,max_ns) if policy else dict(start_ns=0,end_ns=max_ns)
        require(detail is not None and first_global(port['start_local_ns'],clock)>=detail['start_ns'] and
                first_global(port['end_local_ns'],clock)<=detail['end_ns'],
                'Requested boundaries/clock require detailed packet and queue-state capture for this entire interval; only aligned window merging is available, or rerun simulation')
        result[key]='detailed'
    return result


def sample_observation(source,config):
    selected=deepcopy(source if config['observation'] is None else config['observation'])
    if 'period_ns' in config:
        for p in selected['switches']:p['period_ns']=config['period_ns']
    return selected
