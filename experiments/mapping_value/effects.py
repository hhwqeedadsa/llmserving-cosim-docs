"""Internal evaluation of local shifts and real network data/state readiness."""
from collections import defaultdict
from pathlib import Path

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows

from .common import digest, read, require, save
from .evaluate import paired_operations


def readiness(run, layer_id, kind):
    registry=[o for o in stored_rows(run/'truth','operation_registry.jsonl') if o['layer_id']==layer_id and o['kind']==kind]
    require(len(registry)==1,'Need one operation for the selected layer/kind')
    selected=registry[0]['operation_id']; per_rank=defaultdict(dict)
    execution=run/'raw/execution'; actual=iter(iter_result(execution,'transfers'))
    for planned in iter_result(execution,'planned_transfers'):
        if 'ub_task_id' not in planned:
            continue
        transfer=next(actual,None)
        require(transfer is not None and transfer['transfer_id']==planned['transfer_id'], 'Transfer readiness pairing differs')
        if planned['operation_id']!=selected:
            continue
        rank=planned.get('consumer_rank',planned['dst_rank'])
        category=('business_data' if planned['business_payload_bytes'] else
                  'load_ready' if planned.get('message_role')=='ready' else
                  'combine_flag' if kind=='combine' and planned.get('message_role')=='status' else
                  'dispatch_count' if kind=='dispatch' and planned.get('message_role')=='status' else 'other_application')
        record=per_rank[rank].setdefault(category,dict(transfers=0,last_data_ready_ns=0))
        record['transfers']+=1;record['last_data_ready_ns']=max(record['last_data_ready_ns'],transfer['data_ready_ns'])
    require(next(actual,None) is None,'Unmatched actual transfer')
    result=[]
    for rank,categories in sorted(per_rank.items()):
        business=categories.get('business_data',{}).get('last_data_ready_ns')
        state=max((r['last_data_ready_ns'] for name,r in categories.items() if name!='business_data'),default=None)
        result.append(dict(rank_id=rank,categories=categories,
            state_after_last_network_business_ns=None if business is None or state is None else max(0,state-business)))
    return dict(operation_id=selected,layer_id=layer_id,kind=kind,ranks=result,
        scope='network_deliveries_only; same-chip local events and compute availability are not inferred')


def explain(run, reference, layer_id, kind, output):
    run,reference,output=map(Path,(run,reference,output))
    require(not output.exists(),'Effect explanation already exists')
    differences=paired_operations(run,reference)
    current,normal=(read(p/'raw/execution/result.json') for p in (run,reference))
    require(current['normal_exit'] and normal['normal_exit'],'Complete executions required')
    value=dict(schema_version='mapping-value-effects/1',run_manifest_sha256=digest(run/'manifest.json'),
        reference_manifest_sha256=digest(reference/'manifest.json'),
        round_delta_ns=current['service_finished_ns']-normal['service_finished_ns'],
        local_duration_increase_max_ns=max(r['duration_delta_ns'] for r in differences),
        local_completion_shift_max_ns=max(r['completion_shift_ns'] for r in differences),
        local_shift_without_duration_increase=[r for r in differences if r['completion_shift_ns']>100 and abs(r['duration_delta_ns'])<=100],
        readiness=readiness(run,layer_id,kind),reference_readiness=readiness(reference,layer_id,kind),
        qualification='Readiness tails show actual unsatisfied remote application state, not an invented poll or sole critical-path proof. '
                      'Round loss is measured once and never summed over local completion shifts.')
    save(output,value)
    return dict(passed=True,output=str(output),round_delta_ns=value['round_delta_ns'])
