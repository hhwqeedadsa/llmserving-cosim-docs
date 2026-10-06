"""Independently count completed native target deliveries and public aggregates."""
import argparse
from collections import Counter
import json
from pathlib import Path

from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from moe_ub_cosim.user_io import read,save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',type=Path,required=True);p.add_argument('--export',dest='exported',type=Path,required=True)
    p.add_argument('--prepared',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();execution=a.raw/'execution';result=read(execution/'result.json');expected=read(a.prepared)['expected_usage']
    totals=Counter();operation_categories=Counter()
    for t in iter_result(execution,'planned_transfers'):
        totals['transfers']+=1
        if 'ub_task_id' not in t:totals['local_transfers']+=1;continue
        totals['fabric_transfers']+=1;totals['business_bytes']+=t['business_payload_bytes']
        totals['payload_packets']+=(t['payload_bytes']+4095)//4096
        operation_categories[t['operation'],t['message_role'],t['kind']]+=1
        if t['operation']=='LOAD':
            assert t['kind']=='combine' and t['message_role']=='data' and t['src_rank']==t['consumer_rank'] and t['dst_rank']==t['data_owner_rank']
        else:assert t['operation']=='STORE' and t['src_rank']==t['data_owner_rank'] and t['dst_rank']==t['consumer_rank']
    assert all(totals[k]==expected[k] for k in ('transfers','fabric_transfers','local_transfers'))
    assert totals['business_bytes']==10058044416
    assert operation_categories['LOAD','data','combine']==operation_categories['STORE','ready','combine']==935458
    assert totals['payload_packets']==result['resource_lifecycle']['provenance']['native_payloads_retired_at_arrival']
    assert result['record_storage']['fields']['node_execution']['records']==expected['nodes']==9526154
    public=read(a.exported/'metrics/validation.json');assert public['passed'] and public['unique_delivery_bytes']==totals['business_bytes']
    categories=Counter();ports=Counter();queue_rows=silent=0
    for row in stored_rows(a.exported/'truth','message_attribution_windows.csv'):
        categories[row['message_category']]+=row['application_bytes']
    assert set(categories)=={'business_data','attached_metadata','dispatch_count','combine_flag','load_ready'}
    sent=set()
    for row in stored_rows(a.exported/'observed','port_windows.csv'):
        ports[row['switch_id'],row['port_id']]+=row['counted_packets']
        sent.add((row['switch_id'],row['port_id'],row['local_window_start_ns']))
    for plane in range(7):
        for node,choices in [(768+plane,[0,16]),(817+plane,[0,16]),(1104+plane*16,[0,7])]:
            assert all(ports[node,port]>0 for port in choices),(node,choices)
    for row in stored_rows(a.exported/'observed','queue_windows.csv'):
        queue_rows+=1
        if (row['switch_id'],row['port_id'],row['local_window_start_ns']) not in sent:silent+=1
    assert queue_rows>0 and len(ports)==42
    stages=sum(1 for _ in stored_rows(a.exported/'observed','endpoint_stages.jsonl'));assert stages>0
    save(a.output,dict(passed=True,scope='complete_61_block_128_rank_1024_source_first_entry_not_64_rounds',
        totals=dict(totals),operations=[dict(operation=k[0],message_role=k[1],kind=k[2],count=v) for k,v in sorted(operation_categories.items())],
        observed_category_bytes=dict(categories),observed_active_ports=len(ports),observed_planes=7,
        nonzero_queue_windows=queue_rows,queue_windows_without_packet_starts=silent,endpoint_stage_records=stages,
        simulation_end_ns=result['simulation_end_ns'],network_drain=result['network_drain'],hardware_calibrated=False))
    print('PASS full native target delivery counts, ownership, source packet counts and selected aggregate coverage')


if __name__=='__main__':main()
