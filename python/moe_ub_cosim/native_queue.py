"""Bounded native request queue truth; never an observed-only algorithm input."""
from collections import Counter
from .plan import integer, require
from .record_storage import iter_result


def policy(value, ranks, max_ns):
    require(isinstance(value,dict) and set(value)=={'schema_version','rank_ids','end_ns','max_records','max_snapshot_items'}
            and value['schema_version']=='moe-ub-native-queue-config/1','Invalid native queue policy')
    selected=value['rank_ids']
    require(isinstance(selected,list) and selected and all(type(r) is int for r in selected) and
            len(selected)==len(set(selected)) and set(selected)<=set(ranks),'Invalid native queue ranks')
    integer(value['end_ns'],'native queue end',1,max_ns)
    integer(value['max_records'],'native queue records',2*len(selected),2**63-1)
    integer(value['max_snapshot_items'],'native queue snapshot items',1,65536)
    return value


def checked_rows(execution, report):
    capture=report['native_queue_capture'];value=capture['policy']
    policy(value,report['rank_ids'],max(value['end_ns'],capture['end_ns']))
    require(capture['schema_version']=='moe-ub-native-queue-capture/1' and capture['complete'] is True and
            capture['dropped_records']==0 and capture['access_scope']=='internal_truth_not_observed' and
            capture['start_ns']==0 and 0<=capture['end_ns']<=value['end_ns'],'Incomplete native queue capture')
    workers={(w['rank_id'],w['thread_id']):w['node_id'] for w in capture['workers']}
    require(len(workers)==len(capture['workers']) and {r for r,_ in workers}==set(value['rank_ids']),
            'Invalid native worker coverage')
    previous={};count=time=0
    for row in iter_result(execution,'native_queue',report):
        count+=1
        require(row['sequence']==count and time<=row['time_ns']<=capture['end_ns'] and
                row['rank_id'] in value['rank_ids'],'Invalid native queue order/coverage')
        time=row['time_ns'];key=row['rank_id'],row['thread_id']
        require(key in workers and row['node_id']==workers[key],'Unknown native worker identity')
        for field in ('time_ns','sequence','thread_id','node_id','store_inflight','load_inflight',
                      'store_capacity','load_capacity','request_pool_capacity'):
            integer(row[field],field,0,2**63-1)
        require(row['event'] in ('initial','final','task_queued','request_issued','outstanding_blocked',
                                 'request_pool_blocked','response_released'),'Unknown native queue branch')
        require(row['store_inflight']<=row['store_capacity'] and row['load_inflight']<=row['load_capacity'] and
                len(row['pending'])<=row['request_pool_capacity'] and
                len(row['queue'])+len(row['pending'])<=value['max_snapshot_items'],'Impossible native resource state')
        own=Counter(p['operation'] for p in row['pending'] if p['thread_id']==row['thread_id'])
        require((own['STORE'],own['LOAD'])==(row['store_inflight'],row['load_inflight']),
                'Worker occupancy differs from actual pending request owners')
        require(len({p['request_id'] for p in row['pending']})==len(row['pending']),'Duplicate occupied request identifier')
        for task in [*row['queue'],*row['pending'],*([row['affected']] if row['affected'] else [])]:
            require(task['identity']['src_rank']==row['rank_id'] and task['operation'] in ('STORE','LOAD'),
                    'Native task identity is detached from source execution')
        if key not in previous:
            require(row['event']=='initial' and row['time_ns']==0 and not row['pending'] and not row['queue'],
                    'Missing zero-time initial queue state')
        else:require(previous[key]!='final' and row['event']!='initial','Invalid native queue boundary order')
        if row['event']=='outstanding_blocked':
            op=row['affected']['operation'].lower()
            require(row[op+'_inflight']==row[op+'_capacity'] and
                    any(t['task_id']==row['affected']['task_id'] for t in row['queue']),
                    'Outstanding block lacks a full worker and actual queued head')
        if row['event']=='request_pool_blocked':
            require(len(row['pending'])==row['request_pool_capacity'],'Request-pool block without full pool')
        if row['event']=='final':require(row['time_ns']==capture['end_ns'],'Missing exact final queue boundary')
        previous[key]=row['event'];yield row
    require(count==capture['records'] and count<=value['max_records'] and
            set(previous)==set(workers) and all(v=='final' for v in previous.values()),
            'Native queue counts or final coverage mismatch')


def export(execution, report, observation, writer):
    capture=report.get('native_queue_capture')
    require(capture and capture['policy']==observation['native_queue'],'Native queue policy was not captured')
    for row in checked_rows(execution,report):writer.add('truth','native_queue.jsonl',row)
    return capture
