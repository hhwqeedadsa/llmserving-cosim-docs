"""Export packet logs a window at a time; retain only bounded graph registries.

The graph/transfer manifests are still materialized under their separate budget.
No packet history, all-window list or all-window attribution map is retained.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from .observation import (PORT_FIELDS, ATTRIBUTION_FIELDS, iter_json_lines, local_time, public_config,
                          integer, require, validate_observed)
from .research_export import export_metadata, read


class OutputBudget:
    def __init__(self, root, limit):self.root,self.limit,self.used=root,limit,0
    def open(self, name):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        owner=self
        class Stream:
            def __enter__(self):self.stream=path.open('w',newline='');return self
            def write(self, text):
                size=len(text.encode('utf-8'))
                require(size<=owner.limit-owner.used,'Observation export byte budget exceeded')
                count=self.stream.write(text);owner.used+=size;return count
            def __exit__(self,*args):self.stream.close()
        return Stream()
    def json(self,name,value):
        with self.open(name) as stream:stream.write(json.dumps(value,indent=2)+'\n')
    def lines(self,name,values):
        with self.open(name) as stream:
            for value in values:stream.write(json.dumps(value,sort_keys=True)+'\n')


def export_streaming(raw, observation, ranks, max_ns, output):
    require('resource_budget' in observation,'Streaming export requires explicit observation resource budget')
    meta=export_metadata(raw,observation,ranks,max_ns);budget=observation['resource_budget']
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    writer=OutputBudget(output,budget['max_export_bytes'])
    writer.json('observed/public_config.json',public_config(ranks,meta['ports'],budget))
    writer.lines('observed/endpoint_ops.jsonl',meta['endpoints'])
    writer.lines('truth/operation_registry.jsonl',meta['operations'])
    writer.lines('truth/transfer_registry.jsonl',meta['transfers'])
    writer.json('truth/clock_models.json',dict(schema_version='moe-ub-clock-models/1',time_unit='ns',
        drift_unit='parts_per_billion',rounding='floor',formula='floor((1000000000+drift_ppb)*t_ns/1000000000)+offset_ns',
        devices=observation['clocks']))
    audits=[];window_count=zero_count=0;max_attribution=0
    with writer.open('observed/port_windows.csv') as counters, writer.open('truth/attribution_windows.csv') as attribution, \
            writer.open('metrics/byte-classification.jsonl') as categories:
        csv_counters=csv.DictWriter(counters,fieldnames=PORT_FIELDS);csv_counters.writeheader()
        csv_attribution=csv.DictWriter(attribution,fieldnames=ATTRIBUTION_FIELDS);csv_attribution.writeheader()
        for port,directory in zip(meta['ports'],meta['raw_dirs']):
            clock=meta['clocks']['switch',port['switch_id']];start,end,period=(port[k] for k in ('start_local_ns','end_local_ns','period_ns'))
            current=start;packets=whole=included_packets=included_bytes=previous=0
            row_packets=row_bytes=0;classes=Counter();by_operation=Counter()
            native=read(directory/'observation-audit.json')
            audit=read(directory/'resource-audit.json')
            require(audit['budget']==budget,'Backend/export observation budget mismatch')
            event_names=['packet-events.jsonl','egress-queue-audit.jsonl']
            if meta['provenance']:event_names.append('truth/packet_ranges.jsonl')
            event_bytes=sum((directory/name).stat().st_size for name in event_names)
            require(event_bytes==audit['event_bytes']<=budget['max_event_bytes_per_port'],'Raw observation event byte budget/accounting mismatch')
            ranges=iter_json_lines(directory/'truth/packet_ranges.jsonl') if meta['provenance'] else iter(())
            pending=next(ranges,None)
            padding_present=any('payload_regions' in t for t in meta['planned'].values())

            def emit():
                nonlocal current,row_packets,row_bytes,window_count,zero_count,classes,by_operation,max_attribution
                bounds=dict(switch_id=port['switch_id'],port_id=port['port_id'],direction='egress',
                    local_window_start_ns=current,local_window_end_ns=min(current+period,end))
                require(sum(classes.values())==row_bytes,'Per-window whole packet classification mismatch')
                if meta['provenance']:
                    require(sum(by_operation.values())==classes['business_payload_bytes'],'Per-window operation attribution mismatch')
                csv_counters.writerow(dict(**bounds,counted_packets=row_packets,counted_packet_bytes=row_bytes))
                fields=('business_payload_bytes','application_metadata_bytes','data_protocol_overhead_bytes','control_packet_bytes','unattributed_bytes')
                classification={k:classes[k] for k in fields}
                if padding_present:classification['padding_bytes']=classes['padding_bytes']
                categories.write(json.dumps(dict(**bounds,**classification),sort_keys=True)+'\n')
                for operation,value in sorted(by_operation.items()):
                    csv_attribution.writerow(dict(operation_id=operation,**bounds,business_payload_bytes=value))
                max_attribution=max(max_attribution,len(by_operation));window_count+=1;zero_count+=row_packets==0
                current=min(current+period,end);row_packets=row_bytes=0;classes=Counter();by_operation=Counter()

            for event in iter_json_lines(directory/'packet-events.jsonl',budget['max_event_bytes_per_port']):
                require(set(event) in ({'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes'},
                    {'sim_time_ns','packet_uid','counted_packet_bytes','business_payload_bytes','application_metadata_bytes','padding_bytes'}),'Invalid packet event')
                time=integer(event['sim_time_ns'],'packet time',previous,max_ns);previous=time
                size=integer(event['counted_packet_bytes'],'packet bytes',1,2**32-1)
                business=integer(event['business_payload_bytes'],'business bytes',0,size)
                metadata=integer(event.get('application_metadata_bytes',0),'metadata bytes',0,size-business)
                padding=integer(event.get('padding_bytes',0),'padding bytes',0,size-business-metadata)
                packets+=1;whole+=size;local=local_time(time,clock)
                while current<end and local>=min(current+period,end):emit()
                inside=start<=local<end;covered=[];classified=0;attributed=Counter()
                if meta['provenance']:
                    key=time,event['packet_uid'],size
                    while pending is not None and (pending['sim_time_ns'],pending['packet_uid'],pending['counted_packet_bytes'])==key:
                        t=meta['planned'].get(pending['transfer_id']);require(t is not None,'Unknown packet transfer')
                        a,b=pending['task_byte_range'];c,d=pending['packet_byte_range']
                        require(pending['operation_id']==t['operation_id'] and pending['ub_task_id']==t['ub_task_id'] and
                            0<=a<b<=t['payload_bytes'] and 0<c<d<=size and b-a==d-c,'Incorrect packet/task business range')
                        regions=t.get('payload_regions',[dict(begin=0,end=t['payload_bytes'],kind='business')])
                        require(any(r['kind']=='business' and r['begin']<=a<b<=r['end'] for r in regions),'Attributed nonbusiness range')
                        require(not any(c<y and x<d for x,y in covered),'Duplicate packet business range')
                        covered.append((c,d));classified+=b-a;attributed[t['operation_id']]+=b-a
                        pending=next(ranges,None)
                    require(classified==business,'Missing, reordered or duplicate business provenance')
                if inside:
                    row_packets+=1;row_bytes+=size;included_packets+=1;included_bytes+=size
                    classes['business_payload_bytes']+=business;classes['application_metadata_bytes']+=metadata;classes['padding_bytes']+=padding
                    payload=business+metadata+padding
                    classes['data_protocol_overhead_bytes' if payload else 'control_packet_bytes']+=size-payload
                    by_operation.update(attributed)
            require(pending is None,'Extra packet provenance')
            while current<end:emit()
            require(packets==native['all_successful_start_packets'] and whole==native['all_successful_start_bytes']==native['native_tx_bytes_delta'],
                    'Raw port events differ from native counters')
            audits.append(dict(switch_id=port['switch_id'],port_id=port['port_id'],all_counted_packets=packets,
                all_counted_packet_bytes=whole,included_packets=included_packets,included_packet_bytes=included_bytes,
                wire_and_time_fnv1a64=native['wire_and_time_fnv1a64']))
    checked=validate_observed(output/'observed')
    result=dict(schema_version='moe-ub-research-validation/2',passed=True,unique_delivery_bytes=meta['total_business'],
        transfers=len(meta['planned']),endpoint_records=len(meta['endpoints']),port_windows=window_count,zero_windows=zero_count,
        attribution_status='checked' if meta['provenance'] else 'disabled',port_audits=audits,
        byte_classification='byte-classification.jsonl',observed_contract=checked,
        storage=dict(packet_history_retained=False,window_accumulators=1,max_window_operation_entries=max_attribution,
                     graph_registries='materialized_under_separate_run_budget',output_bytes_before_validation=writer.used),
        scope='functional_consistency_not_hardware_calibration')
    writer.json('metrics/validation.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True);parser.add_argument('--observation',type=Path,required=True)
    parser.add_argument('--max-simulation-ns',type=int,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();ranks=sorted(r for g in read(args.raw/'deployment.json')['groups'] for r in g['rank_ids'])
    result=export_streaming(args.raw,read(args.observation),ranks,args.max_simulation_ns,args.output_dir)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
