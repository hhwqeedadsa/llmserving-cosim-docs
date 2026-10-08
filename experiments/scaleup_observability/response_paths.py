"""Retrospective response-path evidence from the six completed LOAD-grain runs."""
import argparse
from collections import defaultdict
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_port_records

from .common import bounded, digest, read, require, save
from .overview import table
from .slot_analysis import csv_rows
from .statistics import distribution
from .workflow import verify_complete


def reconstruct(paths, transfer, links, owner_switches, consumer_device):
    ranges=[];packets=[]
    for uid,points in paths.items():
        points=sorted(points,key=lambda p:p['start_ns'])
        require(len(points)==3 and points[0]['node_id'] in owner_switches,'Need three captured switch departures from data-owner access')
        region=points[0]['task_byte_range']
        require(all(p['task_byte_range']==region for p in points),'Payload range changed on path')
        ranges.append(tuple(region))
        for a,b in zip(points,points[1:]):
            link=links[a['node_id'],a['port_id']]
            require(link['target']==b['node_id'] and b['start_ns']>=a['busy_until_ns']+link['delay_ns'],
                    'Response trace violates topology/serialization/propagation')
        last=points[-1];link=links[last['node_id'],last['port_id']]
        require(link['target']==consumer_device,'Response did not leave toward its consumer')
        packets.append(dict(packet_uid=uid,task_byte_range=region,departures=points,
            reconstructed_arrival_ns=last['busy_until_ns']+link['delay_ns']))
    cursor=0
    for a,b in sorted(ranges):
        require(a==cursor and b>a,'Response bytes have a gap, overlap or duplicate');cursor=b
    require(cursor==transfer['payload_bytes'] and len(paths)==transfer['committed_segments'],'Responses do not cover actual task')
    arrival=max(p['reconstructed_arrival_ns'] for p in packets)
    require(arrival==transfer['data_ready_ns'],'Last-hop delivery does not match actual data-ready event')
    first=min(p['departures'][0]['start_ns'] for p in packets)
    require(transfer['submit_ns']<=first,'Response appeared before task submission')
    ports={(p['departures'][0]['node_id'],p['departures'][0]['port_id']) for p in packets}
    row=dict(transfer_id=transfer['transfer_id'],responses=len(packets),submit_ns=transfer['submit_ns'],
        first_captured_response_ns=first,data_ready_ns=arrival,
        before_first_captured_response_ns=first-transfer['submit_ns'],
        captured_response_completion_span_ns=arrival-first,
        data_latency_ns=arrival-transfer['submit_ns'],owner_access_egress_ports=len(ports),
        first_departure_spread_ns=max(p['departures'][0]['start_ns'] for p in packets)-first,
        last_hop_serialization_min_ns=min(p['departures'][-1]['busy_until_ns']-p['departures'][-1]['start_ns'] for p in packets),
        last_hop_serialization_max_ns=max(p['departures'][-1]['busy_until_ns']-p['departures'][-1]['start_ns'] for p in packets))
    return row,packets


def analyze(batch,output):
    batch,output=Path(batch).resolve(),Path(output).resolve();meta=read(batch/'batch.json')
    require(meta['study']=='load-grain' and len(meta['cases'])==6,'Need complete six-condition grain batch')
    require({(c['load_response_bytes'],c['send_slots_per_rank']) for c in meta['cases']}==
            {(g,s) for g in (128,512,2048) for s in (1,2)},'Unexpected conditions')
    output.mkdir(parents=True,exist_ok=True)
    require(not (output/'summary.json').exists(),'Response-path summary exists')
    reports=[];witness=None;identities=None
    for case in meta['cases']:
        done,summary=verify_complete(batch,case);root=batch/'runs'/case['case_id'];ex=root/'run/raw/execution'
        net=read(root/'run/inputs/network/backend.json')
        require(not net['port_service']['intervals'] and net['attributes']['ns3::UbPort::UbInterframeGap']=='+0ns',
                'Path reconstruction requires normal service and zero interframe gap')
        links={};owner_switches=defaultdict(set)
        kinds={n['node_id']:n['kind'] for n in net['topology']['nodes']}
        for link in net['topology']['links']:
            for a,b in [('src','dst'),('dst','src')]:
                links[link[a],link[a+'_port']]=dict(target=link[b],delay_ns=link['delay_ns'])
                if kinds[link[a]]=='DEVICE':owner_switches[link[a]].add(link[b])
        rank_map=read(ex/'result.json')['rank_to_ub_node']
        rows=csv_rows(root/'overview/transfers.csv',('transfer_id','payload_bytes','committed_segments','submit_ns','data_ready_ns',
                                                   'data_owner_rank','consumer_rank'))
        selected={r['transfer_id']:r for r in rows if r['operation']=='LOAD' and r['payload_bytes']==2048 and r['locality']=='cross_server'}
        require(len(selected)==188,'Expected 188 comparable cross-server LOAD tasks')
        if identities is None:identities=set(selected);witness=min(identities)
        require(set(selected)==identities,'Cross-condition task identities differ')
        paths=defaultdict(lambda:defaultdict(list))
        for folder in sorted((ex/'ports').iterdir()):
            node,port=map(int,folder.name.split('-'))
            audit={}
            for r in iter_port_records(ex,folder,'egress-queue-audit.jsonl'):
                if r['event']=='tx_start':
                    key=r['packet_uid'],r['time_ns']
                    require(key not in audit,'Repeated physical departure identity');audit[key]=r
            for r in iter_port_records(ex,folder,'truth/classified_packet_ranges.jsonl'):
                if r['transfer_id'] not in selected or r['message_category']!='business_data':continue
                physical=audit[r['packet_uid'],r['sim_time_ns']]
                require(physical['packet_bytes']==r['counted_packet_bytes'],'Attribution/physical packet size mismatch')
                paths[r['transfer_id']][r['packet_uid']].append(dict(node_id=node,port_id=port,start_ns=r['sim_time_ns'],
                    busy_until_ns=physical['busy_until_ns'],packet_bytes=physical['packet_bytes'],task_byte_range=r['task_byte_range']))
        require(set(paths)==set(selected),'Missing response paths')
        stats=[];detail=None
        for tid,t in sorted(selected.items()):
            row,packets=reconstruct(paths[tid],t,links,owner_switches[rank_map[t['data_owner_rank']]],rank_map[t['consumer_rank']])
            stats.append(row)
            if tid==witness:detail=dict(summary=row,packets=packets)
        table(output/(case['case_id']+'.csv'),stats)
        save(output/(case['case_id']+'-witness.json'),detail)
        reports.append(dict(case_id=case['case_id'],load_response_bytes=case['load_response_bytes'],send_slots_per_rank=case['send_slots_per_rank'],
            completion=done,tasks=len(stats),witness=detail['summary'],
            distributions={k:distribution([r[k] for r in stats]) for k in
                ('before_first_captured_response_ns','captured_response_completion_span_ns','data_latency_ns',
                 'owner_access_egress_ports','first_departure_spread_ns','last_hop_serialization_min_ns','last_hop_serialization_max_ns')}))
    save(output/'summary.json',dict(schema_version='scaleup-response-paths/1',access='researcher_internal',
        batch_sha256=digest(batch/'batch.json'),retrospective=True,
        selection='All 188 cross-server 2048 B LOADs per condition; witness is minimum transfer_id shared across conditions',
        cases=reports,checks=dict(complete_response_byte_cover=True,physical_departures_match_attribution=True,
            connected_three_switch_paths=True,terminal_arrival_matches_actual_data_ready=True,same_task_identities=True),
        tables={p.name:digest(p) for p in sorted(output.iterdir()) if p.is_file() and p.suffix in ('.csv','.json')},
        qualification='Data response path reconstruction only. Device source departure, request arrival and resource release histories are not fully captured; no isolated attribution of spray versus pipelining.'))


def main():
    if sys.argv[1:2]==['_worker']:
        analyze(*sys.argv[2:]);return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve()
    require(not out.exists(),'Response-path output exists');out.mkdir(parents=True)
    bounded([sys.executable,'-m','experiments.scaleup_observability.response_paths','_worker',args.batch.resolve(),out],
            out/'commands','response-paths',out)
    print('Existing packet paths and actual LOAD data-ready events reconciled; no simulation executed')


if __name__=='__main__':main()
