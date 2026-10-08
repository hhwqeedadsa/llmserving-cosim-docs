"""Passive endpoint send-resource observations, without operation identities."""
from .plan import integer, require

MAX = 2**63-1
VALUES = ('ready_send_count', 'active_send_count', 'send_slot_capacity')
FIELDS = {'rank_id', 'sequence', 'time_local_ns', 'event_kind', *VALUES}
SEMANTICS = dict(schema_version='moe-ub-send-state/1',
    sampling='after_dependency_ready_dispatch_scan_or_slot_configuration',
    ready='non_cpu_COMM_SEND_nodes_left_in_actual_issuable_queue',
    active='actual_p2p_send_resource_occupancy_until_sender_completion',
    zero_capacity='not_configured',
    encoding='changed_states_with_initial_and_final_boundaries',
    tail='drained_state_held_to_declared_capture_end',
    ordering='per_rank_sequence_preserves_same_time_updates')


def validate_policy(policy, ranks):
    require(isinstance(policy, dict) and set(policy)=={'schema_version','max_records_per_rank','max_total_records'} and
            policy['schema_version']=='moe-ub-send-state-config/1', 'Invalid send-state policy')
    for key in ('max_records_per_rank','max_total_records'):integer(policy[key], key, 2, MAX)
    require(policy['max_records_per_rank']<=policy['max_total_records'] and
            policy['max_total_records']>=2*len(ranks), 'Send-state budget cannot cover rank boundaries')
    return policy


def validate_descriptor(value, ranks):
    require(isinstance(value,dict) and set(value)==set(SEMANTICS)|{'policy','coverage'} and
            all(value[k]==v for k,v in SEMANTICS.items()), 'Invalid public send-state semantics')
    validate_policy(value['policy'],ranks)
    coverage=value['coverage']
    require(isinstance(coverage,list) and len(coverage)==len(ranks), 'Invalid send-state coverage')
    total=0
    for item,rank in zip(coverage,ranks):
        require(set(item)=={'rank_id','start_local_ns','end_local_ns','complete','dropped_records','records'} and
                type(item['rank_id']) is int and item['rank_id']==rank and item['complete'] is True and
                type(item['dropped_records']) is int and item['dropped_records']==0, 'Incomplete send-state coverage')
        integer(item['start_local_ns'],'send-state start',-MAX-1,MAX)
        integer(item['end_local_ns'],'send-state end',item['start_local_ns'],MAX)
        total+=integer(item['records'],'send-state records',2,value['policy']['max_records_per_rank'])
    require(total<=value['policy']['max_total_records'],'Send-state total record budget exceeded')
    return {item['rank_id']:item for item in coverage}


def checked_rows(rows, descriptor, ranks):
    """Streaming, observed-only validation; timestamps may coincide after flooring."""
    coverage=validate_descriptor(descriptor,ranks);last={}
    for row in rows:
        require(set(row)==FIELDS and type(row['rank_id']) is int and row['rank_id'] in coverage,
                'Invalid send-state fields/rank')
        rank=row['rank_id'];bounds=coverage[rank];previous=last.get(rank)
        integer(row['sequence'],'send-state sequence',1,bounds['records'])
        integer(row['time_local_ns'],'send-state time',bounds['start_local_ns'],bounds['end_local_ns'])
        for key in VALUES:integer(row[key],key,0,MAX if key=='ready_send_count' else 2**32-1)
        require(row['active_send_count']<=row['send_slot_capacity'] and
                (row['send_slot_capacity'] or not row['ready_send_count']), 'Impossible send-state occupancy')
        kind=row['event_kind'];require(kind in ('initial','update','final'),'Invalid send-state event kind')
        if previous is None:
            require(kind=='initial' and row['sequence']==1 and row['time_local_ns']==bounds['start_local_ns'] and
                    all(row[k]==0 for k in VALUES),'Invalid initial send state')
        else:
            require(previous['event_kind']!='final' and row['sequence']==previous['sequence']+1 and
                    row['time_local_ns']>=previous['time_local_ns'] and kind!='initial','Invalid send-state order')
            if kind=='update':require(any(row[k]!=previous[k] for k in VALUES),'Repeated unchanged send state')
            else:
                require(row['time_local_ns']==bounds['end_local_ns'] and row['sequence']==bounds['records'] and
                        row['ready_send_count']==row['active_send_count']==0 and
                        all(row[k]==previous[k] for k in VALUES),'Invalid final send state')
        last[rank]=dict(row)
        yield row
    require(set(last)==set(coverage) and all(r['event_kind']=='final' for r in last.values()),
            'Missing final send-state boundary')


def export_send_state(execution, report, observation, ranks, max_ns, clocks, writer):
    from .observation import local_time
    from .record_storage import iter_result
    policy=validate_policy(observation['send_state'],ranks)
    capture=report.get('send_state_capture')
    require(isinstance(capture,dict) and set(capture)=={'schema_version','policy','start_ns','end_ns','complete',
            'dropped_records','records','ranks'} and capture['schema_version']=='moe-ub-send-state-capture/1' and
            capture['policy']==policy and type(capture['start_ns']) is int and capture['start_ns']==0 and
            type(capture['end_ns']) is int and capture['end_ns']==max_ns and capture['complete'] is True and
            type(capture['dropped_records']) is int and capture['dropped_records']==0,
            'Send-state observations were not captured completely; rerun simulation')
    require(isinstance(capture['ranks'],list) and len(capture['ranks'])==len(ranks),'Invalid captured send-state ranks')
    coverage=[]
    for item,rank in zip(capture['ranks'],ranks):
        require(set(item)=={'rank_id','records'} and type(item['rank_id']) is int and item['rank_id']==rank,
                'Invalid captured send-state rank')
        coverage.append(dict(**item,start_local_ns=0,end_local_ns=max_ns,complete=True,dropped_records=0))
    require(type(capture['records']) is int and capture['records']==sum(r['records'] for r in coverage),
            'Captured send-state record total mismatch')
    raw_descriptor=dict(**SEMANTICS,policy=policy,coverage=coverage)
    def raw_rows():
        for row in iter_result(execution,'send_state',report):
            require(set(row)==FIELDS-{'time_local_ns'}|{'time_ns'},'Invalid raw send-state fields')
            yield {**{k:v for k,v in row.items() if k!='time_ns'},'time_local_ns':row['time_ns']}
    for row in checked_rows(raw_rows(),raw_descriptor,ranks):
        row['time_local_ns']=local_time(row['time_local_ns'],clocks['rank',row['rank_id']])
        writer.add('observed','endpoint_send_state.jsonl',row)
    public=dict(**SEMANTICS,policy=policy,coverage=[dict(**{k:v for k,v in r.items() if k not in ('start_local_ns','end_local_ns')},
        start_local_ns=local_time(0,clocks['rank',r['rank_id']]),
        end_local_ns=local_time(max_ns,clocks['rank',r['rank_id']])) for r in coverage])
    validate_descriptor(public,ranks)
    return public
