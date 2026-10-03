"""Export local observations and independent truth from passive backend records."""
from collections import Counter
from copy import deepcopy
import json

from .observation import (ATTRIBUTION_FIELDS, ENDPOINT_FIELDS, PORT_FIELDS, integer, json_lines,
    local_time, public_config, require, validate_observation, validate_observed, windows, write_csv, write_lines)


def read(path):
    return json.loads(path.read_text())


def export_records(raw, observation, ranks, max_ns):
    clocks,ports=validate_observation(observation,ranks,max_ns)
    report=read(raw/'execution/result.json');front=read(raw/'service_result.json')
    require(report['normal_exit'] and front['service_finished'] and report['network_drain']['idle'],
            'Cannot export an unfinished run as observations')
    plans=[read(p) for p in sorted((raw/'graphs').glob('*/plan.json'))]
    planned={};operations_by_id={}
    for plan in plans:
        for t in plan['transfers']:
            require(t['transfer_id'] not in planned,'Duplicate planned transfer')
            planned[t['transfer_id']]=t
        for op in plan['operations']:
            require(op['operation_id'] not in operations_by_id,'Duplicate planned operation')
            operations_by_id[op['operation_id']]=op
    local_planned={k:v for k,v in planned.items() if 'ub_task_id' not in v}
    local_actual={t['transfer_id']:t for t in report.get('local_deliveries',[])}
    require(set(local_actual)==set(local_planned),'Local delivery registry mismatch')
    for tid,t in local_planned.items():
        x=local_actual[tid]
        require(x['payload_bytes']==t['payload_bytes'] and x['send_callbacks']==x['recv_callbacks']==1 and not x['active'],
                'Local delivery conservation failed')
    planned={k:v for k,v in planned.items() if 'ub_task_id' in v}
    actual={t['transfer_id']:t for t in report['transfers']}
    require(len(actual)==len(report['transfers']) and set(actual)==set(planned),'Unique transfer registry mismatch')
    delivered=Counter()
    for row in report['target_writes']:delivered[row['task']]+=row['bytes']
    require(set(delivered)=={t['ub_task_id'] for t in planned.values()},'Unexpected unique target write')
    for tid,t in planned.items():
        x=actual[tid]
        require(x['bytes']==x['committed_bytes']==delivered[t['ub_task_id']]==t['payload_bytes'] and
                x['submitted_tasks']==x['send_callbacks']==x['receive_callbacks']==1 and not x['context_active'],
                'Unique delivery or callback conservation failed')
    total_business=sum(t['business_payload_bytes'] for t in planned.values())
    raw_dirs=[raw/f"execution/ports/{p['switch_id']}-{p['port_id']}" for p in ports]
    raw_endpoints=json_lines(raw_dirs[0]/'observed/endpoint_ops.jsonl')
    for directory in raw_dirs[1:]:
        require(json_lines(directory/'observed/endpoint_ops.jsonl')==raw_endpoints,'Port copies disagree on ASTRA endpoints')
    mapped={};endpoints=[];counts=Counter()
    for row in sorted(raw_endpoints,key=lambda r:(r['rank_id'],r['start_local_ns'],r['local_record_id'])):
        require(set(row)==set(ENDPOINT_FIELDS) and row['rank_id'] in ranks,'Invalid raw ASTRA endpoint fields')
        rank=row['rank_id'];key=rank,row['local_record_id']
        require(key not in mapped and 0<=row['start_local_ns']<=row['end_local_ns']<=max_ns,'Invalid raw endpoint interval/identity')
        counts[rank]+=1
        mapped[key]=dict(rank_id=rank,local_record_id=counts[rank],start_global_ns=row['start_local_ns'],
                         end_global_ns=row['end_local_ns'])
        endpoints.append(dict(rank_id=rank,local_record_id=counts[rank],kind=row['kind'],
            start_local_ns=local_time(row['start_local_ns'],clocks['rank',rank]),
            end_local_ns=local_time(row['end_local_ns'],clocks['rank',rank])))
    provenance=observation['record_provenance'];operations=[];transfers=[]
    if provenance:
        operations=json_lines(raw_dirs[0]/'truth/operation_registry.jsonl')
        transfers=json_lines(raw_dirs[0]/'truth/transfer_registry.jsonl')
        require({o['operation_id'] for o in operations}==set(operations_by_id) and
                len(operations)==len(operations_by_id),'Operation registry mismatch')
        require({t['transfer_id'] for t in transfers}==set(planned) and len(transfers)==len(planned),'Transfer registry mismatch')
        for directory in raw_dirs[1:]:
            require(json_lines(directory/'truth/operation_registry.jsonl')==operations and
                    json_lines(directory/'truth/transfer_registry.jsonl')==transfers,'Port registries disagree')
        bound=[]
        for operation in operations:
            for endpoint in operation['endpoint_records']:
                key=endpoint['rank_id'],endpoint['local_record_id'];bound.append(key)
                endpoint.update(mapped[key])
        require(len(bound)==len(set(bound)) and set(bound)==set(mapped),'Missing or duplicate local endpoint binding')
        for t in transfers:
            expected=planned[t['transfer_id']];a=actual[t['transfer_id']]
            require(t['operation_id']==expected['operation_id'] and t['ub_task_id']==expected['ub_task_id'] and
                    t['business_payload_bytes']==expected['business_payload_bytes'],'Transfer source mismatch')
            t['sources']=expected['sources']
            t['execution_global_ns']={k:a[k] for k in ('send_start_ns','receive_start_ns','data_ready_ns',
                                                      'transaction_done_ns','send_done_ns','receive_done_ns')}
    counters=[];categories=[];attribution=[];port_audits=[]
    for port,directory in zip(ports,raw_dirs):
        switch,port_id=port['switch_id'],port['port_id'];clock=clocks['switch',switch]
        bucket=[]
        for start,end in windows(port):
            bucket.append(dict(switch_id=switch,port_id=port_id,direction='egress',
                local_window_start_ns=start,local_window_end_ns=end,counted_packets=0,counted_packet_bytes=0))
        per_class=[dict(business_payload_bytes=0,application_metadata_bytes=0,data_protocol_overhead_bytes=0,
                        control_packet_bytes=0,unattributed_bytes=0) for _ in bucket]
        events=json_lines(directory/'packet-events.jsonl');native=read(directory/'observation-audit.json')
        if any('padding_bytes' in e for e in events):
            for kind in per_class:kind['padding_bytes']=0
        require(len(events)==native['all_successful_start_packets'] and
                sum(e['counted_packet_bytes'] for e in events)==native['all_successful_start_bytes']==native['native_tx_bytes_delta'],
                'Raw port events differ from native counters')
        business_events=Counter();previous=0
        for event in events:
            require(set(event) in ({'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes'},
                {'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes','application_metadata_bytes','padding_bytes'}),'Invalid packet event')
            time=integer(event['sim_time_ns'],'packet global time',previous,max_ns);previous=time
            whole=integer(event['counted_packet_bytes'],'packet bytes',1,2**32-1)
            business=integer(event['business_payload_bytes'],'business bytes',0,whole)
            metadata=integer(event.get('application_metadata_bytes',0),'metadata bytes',0,whole-business)
            padding=integer(event.get('padding_bytes',0),'padding bytes',0,whole-business-metadata)
            if business:business_events[time,event['packet_uid'],whole]+=business
            local=local_time(time,clock)
            if not port['start_local_ns']<=local<port['end_local_ns']:continue
            index=(local-port['start_local_ns'])//port['period_ns']
            row=bucket[index];row['counted_packets']+=1;row['counted_packet_bytes']+=whole
            kind=per_class[index]
            kind['business_payload_bytes']+=business
            kind['application_metadata_bytes']+=metadata
            if 'padding_bytes' in kind:kind['padding_bytes']+=padding
            payload=business+metadata+padding
            kind['data_protocol_overhead_bytes' if payload else 'control_packet_bytes']+=whole-payload
        attributed=Counter();classified=Counter()
        if provenance:
            covered={}
            for event in json_lines(directory/'truth/packet_ranges.jsonl'):
                require(event['transfer_id'] in planned,'Unknown packet transfer')
                t=planned[event['transfer_id']];a,b=event['task_byte_range'];c,d=event['packet_byte_range']
                require(event['operation_id']==t['operation_id'] and event['ub_task_id']==t['ub_task_id'] and
                    0<=a<b<=t['payload_bytes'] and 0<c<d<=event['counted_packet_bytes'] and b-a==d-c,
                    'Incorrect packet/task business byte range')
                regions=t.get('payload_regions',[dict(begin=0,end=t['payload_bytes'],kind='business')])
                require(any(r['kind']=='business' and r['begin']<=a<b<=r['end'] for r in regions),
                        'Metadata or padding was attributed as business data')
                key=event['sim_time_ns'],event['packet_uid'],event['counted_packet_bytes']
                require(not any(c<y and x<d for x,y in covered.get(key,[])), 'Duplicate packet business range')
                covered.setdefault(key,[]).append((c,d));classified[key]+=b-a
                local=local_time(event['sim_time_ns'],clock)
                if port['start_local_ns']<=local<port['end_local_ns']:
                    index=(local-port['start_local_ns'])//port['period_ns']
                    attributed[index,event['operation_id']]+=b-a
            require(classified==business_events,'Missing or duplicate business packet provenance')
        for index,(row,kind) in enumerate(zip(bucket,per_class)):
            require(sum(kind.values())==row['counted_packet_bytes'],'Per-window whole packet classification mismatch')
            if provenance:
                require(sum(v for (i,op),v in attributed.items() if i==index)==kind['business_payload_bytes'],
                        'Per-window operation attribution mismatch')
            categories.append(dict(**{k:row[k] for k in PORT_FIELDS[:5]},**kind))
        for (index,operation),value in sorted(attributed.items()):
            row=bucket[index]
            attribution.append(dict(operation_id=operation,**{k:row[k] for k in PORT_FIELDS[:5]},business_payload_bytes=value))
        counters.extend(bucket)
        port_audits.append(dict(switch_id=switch,port_id=port_id,all_counted_packets=len(events),
            all_counted_packet_bytes=native['native_tx_bytes_delta'],included_packets=sum(r['counted_packets'] for r in bucket),
            included_packet_bytes=sum(r['counted_packet_bytes'] for r in bucket),
            wire_and_time_fnv1a64=native['wire_and_time_fnv1a64']))
    validation=dict(schema_version='moe-ub-research-validation/1',passed=True,unique_delivery_bytes=total_business,
        transfers=len(planned),endpoint_records=len(endpoints),port_windows=len(counters),
        zero_windows=sum(r['counted_packets']==0 for r in counters),
        attribution_status='checked' if provenance else 'disabled',port_audits=port_audits,
        byte_classification=categories,scope='functional_consistency_not_hardware_calibration')
    return dict(public=public_config(ranks,ports),endpoints=endpoints,counters=counters,operations=operations,
        transfers=transfers,attribution=attribution,clocks=dict(schema_version='moe-ub-clock-models/1',
        time_unit='ns',drift_unit='parts_per_billion',rounding='floor',formula='floor((1000000000+drift_ppb)*t_ns/1000000000)+offset_ns',
        devices=deepcopy(observation['clocks'])),validation=validation)


def write_export(directory, records):
    for name in ('observed','truth','metrics'):(directory/name).mkdir(exist_ok=True)
    def save(name,value):(directory/name).write_text(json.dumps(value,indent=2)+'\n')
    save('observed/public_config.json',records['public'])
    write_lines(directory/'observed/endpoint_ops.jsonl',records['endpoints'])
    write_csv(directory/'observed/port_windows.csv',PORT_FIELDS,records['counters'])
    write_lines(directory/'truth/operation_registry.jsonl',records['operations'])
    write_lines(directory/'truth/transfer_registry.jsonl',records['transfers'])
    write_csv(directory/'truth/attribution_windows.csv',ATTRIBUTION_FIELDS,records['attribution'])
    save('truth/clock_models.json',records['clocks'])
    check=validate_observed(directory/'observed')
    require(check['endpoint_records']==len(records['endpoints']),'Observed validation record count')
    records['validation']['observed_contract']=check
    save('metrics/validation.json',records['validation'])
