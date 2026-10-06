"""Independent research conservation checks over streamed result/2 records."""
from collections import Counter
import json

from .observation import ENDPOINT_FIELDS,local_time,validate_observation
from .plan import integer,require
from .record_storage import iter_result,validate_policy
from .resources import check_graph,check_run,validate_budget


def read(path):return json.loads(path.read_text())


def export_metadata(raw,observation,ranks,max_ns,writer,index,limits):
    clocks,ports=validate_observation(observation,ranks,max_ns)
    execution=raw/'execution';report=read(execution/'result.json');front=read(raw/'service_result.json')
    require(report['schema_version']=='moe-ub-service-result/2' and report['normal_exit'] and
            front['service_finished'] and report['network_drain']['idle'],'Bounded export requires a completed result/2 run')
    storage=report['record_storage'];record_policy=validate_policy(storage['policy'])
    require(storage['fields']['endpoint_ops']['records']<=limits['max_endpoint_records'],'Endpoint index budget exceeded')
    planned_ops={};used=dict(graphs=0,transfers=0,nodes=0);graph_ids=set();budget=None
    if 'graph_resources' in report:
        require(report['graph_resources']==front.get('graph_resources'),'Frontend/backend graph resource mismatch')
        budget=validate_budget(report['graph_resources']['budget'])
    # KV/batch histories were needed only for the front completion/resource
    # checks above. Do not retain them while scanning every transfer and port.
    del front
    declared_transfers=declared_nodes=0
    for summary in iter_result(execution,'plan_summary',report):
        require(summary['graph_id'] not in graph_ids,'Repeated graph summary');graph_ids.add(summary['graph_id'])
        require(len(graph_ids)<=limits['max_graphs'],'Graph summary budget exceeded')
        require(summary['resource_budget']==budget,'Graph summary resource budget mismatch')
        if budget is not None:used=check_run(used,check_graph(summary['resource_usage'],budget),budget)
        declared_transfers+=summary['resource_usage']['transfers'];declared_nodes+=summary['resource_usage']['nodes']
        for operation in summary['operations']:
            name=operation['operation_id']
            require(name not in planned_ops and len(planned_ops)<limits['max_operations'],'Duplicate operation or operation budget exceeded')
            planned_ops[name]=operation
    if budget is not None:require(used==report['graph_resources']['used'],'Graph resource accounting mismatch')
    require(declared_transfers==storage['fields']['planned_transfers']['records'] and
        declared_nodes==storage['fields']['node_execution']['records'],'Graph record totals mismatch')

    endpoints=list(iter_result(execution,'endpoint_ops',report));mapped={};counts=Counter()
    require(len(endpoints)<=limits['max_endpoint_records'],'Endpoint index budget exceeded')
    for row in sorted(endpoints,key=lambda r:(r['rank_id'],r['start_local_ns'],r['local_record_id'])):
        require(set(row)==set(ENDPOINT_FIELDS) and row['rank_id'] in ranks,'Invalid raw endpoint fields')
        rank=row['rank_id'];key=rank,row['local_record_id']
        require(key not in mapped and 0<=row['start_local_ns']<=row['end_local_ns']<=max_ns,'Invalid raw endpoint interval/identity')
        counts[rank]+=1;mapped[key]=dict(rank_id=rank,local_record_id=counts[rank],
            start_global_ns=row['start_local_ns'],end_global_ns=row['end_local_ns'])
        writer.add('observed','endpoint_ops.jsonl',dict(rank_id=rank,local_record_id=counts[rank],kind=row['kind'],
            start_local_ns=local_time(row['start_local_ns'],clocks['rank',rank]),
            end_local_ns=local_time(row['end_local_ns'],clocks['rank',rank])))
    del endpoints
    provenance=observation['record_provenance'];bound=set();operations=set()
    for operation in iter_result(execution,'operation_registry',report):
        require(provenance,'Unexpected source registry with provenance disabled')
        name=operation['operation_id'];require(name in planned_ops and name not in operations,'Operation registry mismatch')
        operations.add(name)
        for endpoint in operation['endpoint_records']:
            key=endpoint['rank_id'],endpoint['local_record_id']
            require(key in mapped and key not in bound,'Missing or duplicate endpoint binding')
            bound.add(key);endpoint.update(mapped[key])
        writer.add('truth','operation_registry.jsonl',operation)
    if provenance:
        require(operations==set(planned_ops) and bound==set(mapped),'Incomplete operation/endpoint binding')
    endpoint_count=len(mapped)
    if 'statistics' in observation:
        from .operation_stages import export_stages
        export_stages(execution,report,mapped,clocks,writer,max_ns)
    del mapped,bound

    actual=iter(iter_result(execution,'transfers',report));local=iter(iter_result(execution,'local_deliveries',report))
    registry=iter(iter_result(execution,'transfer_registry',report))
    total_business=count=0;last_id=-1;padding_present=False
    for transfer in iter_result(execution,'planned_transfers',report):
        tid=integer(transfer['transfer_id'],'planned transfer id',1,2**64-1)
        require(tid>last_id and transfer['operation_id'] in planned_ops,'Unordered transfer or unknown planned operation')
        last_id=tid;count+=1
        if 'ub_task_id' not in transfer:
            row=next(local,None)
            require(row is not None and row['transfer_id']==tid and row['payload_bytes']==transfer['payload_bytes'] and
                row['send_callbacks']==row['recv_callbacks']==1 and not row['active'],'Local delivery conservation failed')
            continue
        row=next(actual,None)
        require(row is not None and row['transfer_id']==tid and
            row['bytes']==row['committed_bytes']==transfer['payload_bytes'] and
            row['submitted_tasks']==row['send_callbacks']==row['receive_callbacks']==1 and not row['context_active'],
            'Unique delivery or callback conservation failed')
        if transfer.get('operation') in ('STORE','LOAD'):
            require(row['operation']==transfer['operation'] and row['initiator_resources_retired'] and
                    row['data_owner_rank']==transfer['data_owner_rank'] and
                    row['consumer_rank']==transfer['consumer_rank'], 'Native operation or retirement mismatch')
        index.add(transfer);total_business+=transfer['business_payload_bytes']
        padding_present|='payload_regions' in transfer
        if provenance:
            registered=next(registry,None)
            require(registered is not None and registered['transfer_id']==tid and
                all(registered[k]==transfer[k] for k in ('operation_id','ub_task_id','business_payload_bytes')),
                'Transfer source mismatch')
            registered['sources']=transfer['sources']
            registered['execution_global_ns']={k:row[k] for k in ('send_start_ns','receive_start_ns','data_ready_ns',
                'transaction_done_ns','send_done_ns','receive_done_ns')}
            writer.add('truth','transfer_registry.jsonl',registered)
    require(count==declared_transfers and next(actual,None) is None and next(local,None) is None and
        next(registry,None) is None,'Extra or missing execution transfer records')
    index.seal()
    for row in iter_result(execution,'target_writes',report):index.add_write(row['task'],row['bytes'])
    for field,completed in (('native_starts',False),('native_completions',True)):
        if field in storage['fields']:
            for row in iter_result(execution,field,report):index.native_event(row['task'],completed=completed)
    for ordinal in range(index.count):
        row=index._row(ordinal)
        require(row[-1]==row[3],'Unique target write byte conservation failed')
    raw_dirs=[execution/f"ports/{p['switch_id']}-{p['port_id']}" for p in ports]
    expected={'observed/endpoint_ops.jsonl':'endpoint_ops'}
    if provenance:expected.update({'truth/operation_registry.jsonl':'operation_registry','truth/transfer_registry.jsonl':'transfer_registry'})
    for directory in raw_dirs:
        reference=read(directory/'registry-records.json')
        require(reference==dict(schema_version='moe-ub-observation-registry/2',
            path_base='execution_directory',result='result.json',fields=expected),'Port registry references disagree')
    return dict(clocks=clocks,ports=ports,provenance=provenance,raw_dirs=raw_dirs,
        execution=execution,report=report,record_policy=record_policy,graphs=len(graph_ids),
        total_business=total_business,endpoint_count=endpoint_count,padding_present=padding_present)
