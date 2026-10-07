"""Normal-only selection of state/business windows and an overlap control."""
from collections import defaultdict
import math
from pathlib import Path

from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save


def freeze_followups(baseline, endpoint_run, output):
    baseline,endpoint_run,output=map(lambda p:Path(p).resolve(),(baseline,endpoint_run,output))
    require(not output.exists(),'Follow-up freeze output already exists')
    normal=baseline/'r000'; messages=list(stored_rows(normal/'truth','message_attribution_windows.csv'))
    operations={r['operation_id']:r for r in stored_rows(normal/'truth','operation_registry.jsonl')}
    business=defaultdict(int); application=defaultdict(int); last_business=defaultdict(int)
    for row in messages:
        key=row['switch_id'],row['port_id'],row['local_window_start_ns']
        application[key]+=row['application_bytes']
        if row['message_category']=='business_data':
            business[key]+=row['application_bytes']
            last_business[row['operation_id']]=max(last_business[row['operation_id']],row['local_window_end_ns'])
    eligible=[row for row in messages if row['message_category']=='combine_flag' and
        operations[row['operation_id']]['layer_id']==2 and
        not business[row['switch_id'],row['port_id'],row['local_window_start_ns']] and
        row['local_window_start_ns']>=last_business[row['operation_id']]]
    require(eligible,'No normal post-business state-only window; do not invent one')
    state=min(eligible,key=lambda r:(r['local_window_start_ns'],r['switch_id'],r['port_id']))
    vectors=[row for row in messages if row['operation_id']==state['operation_id'] and
             row['message_category']=='business_data' and
             (row['switch_id'],row['port_id'])==(state['switch_id'],state['port_id'])]
    require(vectors,'Selected physical port lacks same-call business contrast')
    vector=min(vectors,key=lambda r:(-r['application_bytes'],r['local_window_start_ns']))
    def interval(row):
        return dict(node_id=row['switch_id'],port_id=row['port_id'],start_ns=row['local_window_start_ns'],
                    end_ns=row['local_window_end_ns'],rate_bps=56_000_000_000)
    cases=[dict(case_id='r006',purpose='normal_post_business_state_window',endpoint_prepare=[],port_service=[interval(state)]),
           dict(case_id='r007',purpose='same_port_business_window',endpoint_prepare=[],port_service=[interval(vector)])]
    endpoint=read(baseline/'frozen/r001.json')['endpoint_prepare']
    stages={r['stage']:r['time_local_ns'] for r in stored_rows(normal/'observed','endpoint_stages.jsonl')
            if r['rank_id']==3 and r['local_record_id']==3}
    start=math.ceil(stages['prepare_start']/100)*100
    end=stages['prepare_end']+4*endpoint[0]['dispatch_ns_per_local_token']
    ports=read(normal/'observed/public_config.json')['ports']
    empty=[(t,p['switch_id'],p['port_id']) for t in range(start,end-99,100) for p in ports
           if not application[p['switch_id'],p['port_id'],t]]
    require(empty,'No normal application-empty overlap interval')
    t,node,port=min(empty)
    cases.append(dict(case_id='r008',purpose='overlap_with_normal_application_empty_window',endpoint_prepare=endpoint,
        port_service=[dict(node_id=node,port_id=port,start_ns=t,end_ns=t+100,rate_bps=56_000_000_000)],
        paired_reference_manifest_sha256=digest(endpoint_run/'manifest.json'),evaluated_components=['port_service']))
    output.mkdir(parents=True)
    for case in cases:
        case.update(schema_version='mapping-value-case/1',normal_manifest_sha256=digest(normal/'manifest.json'))
        save(output/(case['case_id']+'.json'),case)
    save(output/'freeze.json',dict(schema_version='mapping-value-followups/1',normal_manifest_sha256=digest(normal/'manifest.json'),
        rules=dict(state='earliest layer-2 combine_flag window after last business window; no business from any call in window',
            business='same port and call, greatest application business bytes then earliest window',
            overlap='first normal application-empty 100 ns port window in the declared extended preparation interval'),
        state_window=state,business_window=vector,overlap_interval_ns=[t,t+100],
        normal_prepare_ns=[stages['prepare_start'],stages['prepare_end']],
        comparison='r008 versus existing r001 removes only network intervention; do not sum effects',
        cases={c['case_id']:digest(output/(c['case_id']+'.json')) for c in cases}))
    return dict(passed=True,output=str(output),cases=[c['case_id'] for c in cases])
