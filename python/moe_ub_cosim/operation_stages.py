"""Public per-rank stage events; global operation identities stay in raw/truth."""
from .observation import local_time,I64_MIN,I64_MAX
from .plan import integer,require
from .record_storage import iter_result

STAGE_FIELDS={'rank_id','local_record_id','stage','time_local_ns','event_count'}
STAGES={'prepare_start','prepare_end','local_prepare_start','local_prepare_end',
        'request_submit_first','request_submit_last','data_ready','initiator_resources_complete',
        'local_publish_first','local_publish_last','local_resources_complete','operation_complete'}


def export_stages(execution,report,mapped,clocks,writer,max_ns):
    require('operation_stages' in report['record_storage']['fields'],
            'Operation stages were not captured; rerun with statistics enabled')
    for row in iter_result(execution,'operation_stages',report):
        require(set(row)=={'graph_id','operation_id','rank_id','local_record_id','stage','time_ns','event_count'},
                'Invalid raw operation stage fields')
        key=row['rank_id'],row['local_record_id'];require(key in mapped,'Unknown raw stage endpoint')
        integer(row['time_ns'],'stage time',0,max_ns)
        writer.add('observed','endpoint_stages.jsonl',dict(rank_id=key[0],local_record_id=mapped[key]['local_record_id'],
            stage=row['stage'],time_local_ns=local_time(row['time_ns'],clocks['rank',key[0]]),event_count=row['event_count']))


def validate_stages(rows,endpoints):
    operations={(r['rank_id'],r['local_record_id']):r for r in endpoints};seen={}
    for row in rows:
        require(set(row)==STAGE_FIELDS and row['stage'] in STAGES,'Unknown or internal public stage fields')
        integer(row['rank_id'],'stage rank',0,2**32-1);integer(row['local_record_id'],'stage local id',1)
        integer(row['time_local_ns'],'stage local time',I64_MIN,I64_MAX);integer(row['event_count'],'stage count',1,2**64-1)
        key=row['rank_id'],row['local_record_id'];require(key in operations,'Stage lacks local endpoint record')
        stages=seen.setdefault(key,{})
        require(row['stage'] not in stages,'Duplicate operation stage');stages[row['stage']]=row
    require(set(seen)==set(operations),'Missing operation stage records')
    for key,stages in seen.items():
        complete=stages.get('operation_complete')
        require(complete is not None and complete['event_count']==1 and
                complete['time_local_ns']==operations[key]['end_local_ns'],'Operation completion differs from endpoint interval')
        for a,b,single in [('prepare_start','prepare_end',True),('local_prepare_start','local_prepare_end',True),
                           ('request_submit_first','request_submit_last',False),('local_publish_first','local_publish_last',False)]:
            require((a in stages)==(b in stages),'Incomplete stage pair')
            if a in stages:
                x,y=stages[a],stages[b]
                require(x['time_local_ns']<=y['time_local_ns'] and x['event_count']==y['event_count'] and
                        (not single or x['event_count']==1),'Invalid stage interval/count')
        for submit,done in [('request_submit_last','initiator_resources_complete'),('local_publish_last','local_resources_complete')]:
            require((submit in stages)==(done in stages),'Missing initiator resource completion')
            if submit in stages:
                require(stages[submit]['event_count']==stages[done]['event_count'] and
                        stages[submit]['time_local_ns']<=stages[done]['time_local_ns'],'Resources complete before submission')
    return sum(len(value) for value in seen.values())
