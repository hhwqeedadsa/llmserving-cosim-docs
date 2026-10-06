"""Export compressed research records with finite metadata and disk budgets."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .bounded_metadata import export_metadata,read
from .observation import PORT_FIELDS,local_time,public_config
from .plan import integer,require
from .plan_io import file_sha256
from .record_storage import iter_file
from .research_storage import ResearchWriter,validate_observed_storage
from .transfer_index import TransferIndex,ROW

LIMITS={'max_index_bytes','max_compressed_bytes','max_record_bytes','max_endpoint_records',
        'max_operations','max_layouts','max_id_ranges','max_graphs','cache_rows'}


def policy(value):
    require(set(value)==LIMITS|{'schema_version'} and value['schema_version']=='moe-ub-research-export-policy/1',
            'Invalid bounded research policy')
    for key in LIMITS:integer(value[key],key,1)
    require(value['max_record_bytes']<=64*2**20,'Research record budget exceeds supported bound')
    return value


def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def export_bounded(raw,observation,ranks,max_ns,output,limits):
    limits=policy(limits);raw=Path(raw);output=Path(output)
    writer=ResearchWriter(output,limits['max_compressed_bytes'],limits['max_record_bytes'])
    fields={'observed':['endpoint_ops.jsonl','port_windows.csv'],
            'truth':['operation_registry.jsonl','transfer_registry.jsonl','attribution_windows.csv'],
            'metrics':['byte-classification.jsonl']}
    statistics='statistics' in observation
    if statistics:fields['observed']+=['queue_windows.csv','endpoint_stages.jsonl']
    index_path=output/'metrics/transfer-index.bin'
    try:
        for scope,names in fields.items():
            for name in names:writer.open(scope,name)
        save(output/'metrics/export-policy.json',limits)
        with TransferIndex(index_path,max_bytes=limits['max_index_bytes'],max_operations=limits['max_operations'],
                max_layouts=limits['max_layouts'],max_id_ranges=limits['max_id_ranges'],cache_rows=limits['cache_rows']) as index:
            meta=export_metadata(raw,observation,ranks,max_ns,writer,index,limits)
            save(output/'observed/public_config.json',public_config(ranks,meta['ports'],observation.get('resource_budget'),statistics=statistics))
            save(output/'truth/clock_models.json',dict(schema_version='moe-ub-clock-models/1',time_unit='ns',
                drift_unit='parts_per_billion',rounding='floor',formula='floor((1000000000+drift_ppb)*t_ns/1000000000)+offset_ns',
                devices=observation['clocks']))
            audits=[];total_windows=nonzero_windows=max_attribution=0
            for port,directory in zip(meta['ports'],meta['raw_dirs']):
                clock=meta['clocks']['switch',port['switch_id']];start,end,period=(port[k] for k in ('start_local_ns','end_local_ns','period_ns'))
                total_windows+=(end-start-1)//period+1
                native=read(directory/'observation-audit.json');audit=read(directory/'resource-audit.json')
                manifest=read(directory/'record-storage.json')
                require(manifest['schema_version'] in ('moe-ub-port-records/1','moe-ub-port-records/2') and manifest['path_base']=='execution_directory' and
                    manifest['records_directory']=='records','Invalid raw port storage')
                require(audit['budget']==observation.get('resource_budget'),'Backend/export observation budget mismatch')
                event_names=['packet-events.jsonl','egress-queue-audit.jsonl']
                if manifest['schema_version']=='moe-ub-port-records/2':event_names.append('queue-state-events.jsonl')
                if meta['provenance']:event_names.append('truth/packet_ranges.jsonl')
                raw_bytes=sum(manifest['fields'][name]['raw_bytes'] for name in event_names)
                require(raw_bytes==audit['event_bytes'],'Raw event byte accounting mismatch')
                if 'resource_budget' in observation:
                    require(raw_bytes<=observation['resource_budget']['max_event_bytes_per_port'],'Raw port byte budget exceeded')
                def records(name):
                    return iter_file(meta['execution'],manifest['fields'][name],meta['record_policy']['max_record_bytes'])
                if statistics:
                    from .queue_statistics import export_queue
                    export_queue(meta,port,manifest,records,writer,max_ns)
                ranges=iter(records('truth/packet_ranges.jsonl')) if meta['provenance'] else iter(())
                pending=next(ranges,None);current=None;row_packets=row_bytes=0
                classes=Counter();by_operation=Counter();previous=packets=whole=included_packets=included_bytes=0

                def emit():
                    nonlocal current,row_packets,row_bytes,classes,by_operation,nonzero_windows,max_attribution
                    if current is None:return
                    left=start+current*period
                    bounds=dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',
                        local_window_start_ns=left,local_window_end_ns=min(left+period,end))
                    require(sum(classes.values())==row_bytes,'Per-window whole packet classification mismatch')
                    if meta['provenance']:
                        require(sum(by_operation.values())==classes['business_payload_bytes'],'Per-window operation attribution mismatch')
                    writer.add('observed','port_windows.csv',dict(**bounds,counted_packets=row_packets,counted_packet_bytes=row_bytes))
                    category={k:classes[k] for k in ('business_payload_bytes','application_metadata_bytes',
                        'data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes')}
                    if meta['padding_present']:category['padding_bytes']=classes['padding_bytes']
                    writer.add('metrics','byte-classification.jsonl',dict(**bounds,**category))
                    for operation,value in sorted(by_operation.items()):
                        writer.add('truth','attribution_windows.csv',dict(operation_id=operation,**bounds,business_payload_bytes=value))
                    max_attribution=max(max_attribution,len(by_operation));nonzero_windows+=1
                    current=None;row_packets=row_bytes=0;classes=Counter();by_operation=Counter()

                for event in records('packet-events.jsonl'):
                    require(set(event) in ({'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes'},
                        {'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes','application_metadata_bytes','padding_bytes'}),
                        'Invalid packet event')
                    time=integer(event['sim_time_ns'],'packet time',previous,max_ns);previous=time
                    integer(event['packet_uid'],'packet uid',0,2**64-1)
                    size=integer(event['counted_packet_bytes'],'packet bytes',1,2**32-1)
                    business=integer(event['business_payload_bytes'],'business bytes',0,size)
                    metadata=integer(event.get('application_metadata_bytes',0),'metadata bytes',0,size-business)
                    padding=integer(event.get('padding_bytes',0),'padding bytes',0,size-business-metadata)
                    packets+=1;whole+=size;local=local_time(time,clock);inside=start<=local<end
                    covered=[];classified=0;attributed=Counter()
                    if meta['provenance']:
                        key=time,event['packet_uid'],size
                        while pending is not None and (pending['sim_time_ns'],pending['packet_uid'],pending['counted_packet_bytes'])==key:
                            transfer=index.get(pending['transfer_id']);require(transfer is not None,'Unknown packet transfer')
                            a,b=pending['task_byte_range'];c,d=pending['packet_byte_range']
                            require(pending['operation_id']==transfer['operation_id'] and pending['ub_task_id']==transfer['ub_task_id'] and
                                0<=a<b<=transfer['payload_bytes'] and 0<c<d<=size and b-a==d-c,'Incorrect packet/task business range')
                            require(any(r['kind']=='business' and r['begin']<=a<b<=r['end'] for r in transfer['payload_regions']),
                                    'Attributed nonbusiness range')
                            require(not any(c<y and x<d for x,y in covered),'Duplicate packet business range')
                            covered.append((c,d));classified+=b-a;attributed[transfer['operation_id']]+=b-a
                            pending=next(ranges,None)
                        require(classified==business,'Missing, reordered or duplicate business provenance')
                    if inside:
                        selected=(local-start)//period
                        if current is not None and selected!=current:emit()
                        current=selected;row_packets+=1;row_bytes+=size;included_packets+=1;included_bytes+=size
                        classes['business_payload_bytes']+=business;classes['application_metadata_bytes']+=metadata;classes['padding_bytes']+=padding
                        payload=business+metadata+padding
                        classes['data_protocol_overhead_bytes' if payload else 'control_packet_bytes']+=size-payload
                        by_operation.update(attributed)
                require(pending is None,'Extra packet provenance');emit()
                require(packets==native['all_successful_start_packets'] and
                    whole==native['all_successful_start_bytes']==native['native_tx_bytes_delta'],'Raw events differ from native counters')
                audits.append(dict(switch_id=port['switch_id'],port_id=port['port_id'],all_counted_packets=packets,
                    all_counted_packet_bytes=whole,included_packets=included_packets,included_packet_bytes=included_bytes,
                    wire_and_time_fnv1a64=native['wire_and_time_fnv1a64']))
            writer.finish(total_windows)
            checked=validate_observed_storage(output/'observed')
            result=dict(schema_version='moe-ub-research-validation/3',passed=True,unique_delivery_bytes=meta['total_business'],
                transfers=index.count,endpoint_records=meta['endpoint_count'],port_windows=total_windows,
                zero_windows=total_windows-nonzero_windows,attribution_status='checked' if meta['provenance'] else 'disabled',
                port_audits=audits,observed_contract=checked,scope='functional_consistency_not_hardware_calibration',
                storage=dict(schema_version='moe-ub-research-storage/1',compressed_record_bytes=writer.used,
                    index_bytes=index.count*ROW.size,index_row_bytes=ROW.size,index_identity_ranges=len(index.ranges),
                    index_transfer_ranges=len(index.transfer_starts),
                    index_layouts=len(index.layouts),max_cached_transfer_rows=limits['cache_rows'],
                    window_accumulators=1,max_window_operation_entries=max_attribution,
                    classification_window_encoding='nonzero_with_public_declared_zero_windows',padding_field=meta['padding_present']))
        # A successful independent check no longer needs its reproducible index.
        save(output/'metrics/index-cleanup.json',dict(path='metrics/transfer-index.bin',bytes=index_path.stat().st_size,
            sha256=file_sha256(index_path),reason='completed_export_validation',raw_input=str(raw),policy=limits))
        index_path.unlink();save(output/'metrics/validation.json',result)
        return result
    except Exception as error:
        (output/'metrics').mkdir(exist_ok=True)
        save(output/'metrics/failure.json',dict(passed=False,error=str(error),error_type=type(error).__name__))
        raise
    finally:writer.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('raw','observation','policy','output-dir'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--max-simulation-ns',type=int,required=True);args=parser.parse_args()
    ranks=sorted(r for group in read(args.raw/'deployment.json')['groups'] for r in group['rank_ids'])
    result=export_bounded(args.raw,read(args.observation),ranks,args.max_simulation_ns,args.output_dir,read(args.policy))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
