"""Explain observed tails using saved work, actual TX and reference CNP history."""
import argparse
from collections import Counter
import json
from pathlib import Path

CASES=('gbn-normal','gbn-local','sr-normal','sr-local')


def load(p):return json.loads(p.read_bytes())


def diagnose(root,output):
    cases={};group1_traffic={};fault_attempts={}
    for name in CASES:
        r=load(root/name/'result.json');native=r['service_transport']['native']
        messages=r['service_transport']['messages'];config=load(root/name/'input.json');groups=config['parallel']['groups']
        metadata={m['id']:dict(m,group_index=w['group_index'],graph_id=w['graph_id'])
            for p in (root/name/'graphs').glob('*/wave.json') for w in [load(p)] for m in w['messages']}
        packets=[dict(zip(native['source_packet_columns'],row)) for row in native['source_packets']]
        group1_traffic[name]=Counter((p['stream'],p['sequence'],p['payload_bytes']) for p in packets if p['src'] in groups[1])
        fault_attempts[name]=Counter();endpoint_arrivals=endpoint_drops=0
        for row in r['packet_capture']['events']:
            p=dict(zip(r['packet_capture']['columns'],row))
            if p['event']==2 and p['ip_protocol']==17:
                fault_attempts[name][p['message_id'],p['sequence'],p['data_payload_bytes']]+=1
            if p['node']<r['gpu_count'] and p['ip_protocol']==17 and p['event'] in (1,2):
                endpoint_arrivals+=1;endpoint_drops+=p['event']==2
        tail=[]
        for gi in (0,1):
            subset=[m for m in messages if metadata[m['id']]['group_index']==gi]
            last=max(m['ack_ns'] for m in subset)
            for m in subset:
                if m['ack_ns']!=last:continue
                tx=[p for p in packets if p['stream']==m['stream'] and m['begin']<=p['sequence']<m['end']]
                ref=[e for e in native['dcqcn_reference']['events'] if e['stream']==m['stream'] and e['time_ns']<=m['ack_ns']]
                cnp=[dict(time_ns=e['time_ns'],rate_bps=e['rate_bps']) for e in ref if e['kind']=='cnp_received']
                rates=[dict(zip(native['source_rate_capture']['columns'],row)) for row in native['source_rate_capture']['samples']
                    if row[1]==m['stream']]
                tx_rates=[p for p in rates if p['event']==0 and m['begin']<=p['sequence']<m['end']]
                if len(tx)!=len(tx_rates):raise ValueError('source rate/TX record count differs')
                prior=[w for w in native['work'] if w['stream']==m['stream'] and w['end']<=m['begin']
                    and w['acked_ns']>m['published_ns']]
                sent_before=sorted((p['sequence'],p['sequence']+p['payload_bytes']) for p in packets
                    if p['stream']==m['stream'] and p['time_ns']<m['published_ns'])
                sent_prefix=0
                for begin,end in sent_before:
                    if begin>sent_prefix:raise ValueError('source unique prefix has a gap')
                    sent_prefix=max(sent_prefix,end)
                tail.append(dict(group=gi,graph_id=metadata[m['id']]['graph_id'],message_id=m['id'],
                    stream=m['stream'],src=m['src'],dst=m['dst'],phase=m['phase'],layer=m['layer'],
                    ready_ns=m['ready_ns'],published_ns=m['published_ns'],first_tx_ns=min(p['time_ns'] for p in tx),
                    last_tx_ns=max(p['time_ns'] for p in tx),receive_ready_ns=m['receive_ready_ns'],ack_ns=m['ack_ns'],
                    logical_bytes=m['bytes'],actual_source_packets=len(tx),
                    source_payload_bytes=sum(p['payload_bytes'] for p in tx),
                    first_send_wait_ns=min(p['time_ns'] for p in tx)-m['published_ns'],
                    actual_sending_span_ns=max(p['time_ns'] for p in tx)-min(p['time_ns'] for p in tx),
                    min_actual_tx_rate_bps=min(p['rate_bps'] for p in tx_rates),
                    min_historical_reference_rate_bps=min(e['rate_bps'] for e in ref),cnp_received=cnp,
                    source_unique_prefix_before_publication=sent_prefix,
                    earlier_unsent_bytes_at_publication=max(0,m['begin']-sent_prefix),
                    earlier_unconfirmed_work=[dict(message_id=w['id'],graph_id=metadata[w['id']]['graph_id'],
                        phase=metadata[w['id']]['phase'],layer=metadata[w['id']]['layer'],
                        begin=w['begin'],end=w['end'],ready_ns=w['ready_ns'],acked_ns=w['acked_ns']) for w in prior],
                    source_tx_samples=tx_rates))
        recovery=native['recovery'];codes=recovery['event_codes'];counts=Counter(row[1] for row in recovery['events'])
        cases[name]=dict(tails=tail,timeout_recovery_requests=counts[codes['timeout_retransmit']],
            all_endpoint_data_arrival_attempts=endpoint_arrivals,all_endpoint_data_drops=endpoint_drops,
            all_endpoint_observed_drop_fraction=endpoint_drops/endpoint_arrivals,
            sr_max_per_stream_peak_reorder_bytes=max((s['peak_reorder_bytes'] for s in
                native.get('selective_repeat',{}).get('states',[])),default=None),
            pfc_pause=sum(p['pfc_pause'] for p in r['ports']),
            pfc_resume=sum(p['pfc_resume'] for p in r['ports']),
            all_switch_drop_counts=r['switch_drop_capture']['all_switch_reason_counts'],
            reference_feedback_counts=dict(Counter(e['kind'] for e in native['dcqcn_reference']['events'])))
    record=dict(schema_version='moe-local-transient-diagnosis/1',network_runs=0,cases=cases,
        local_group1_same_source_sequence_size_multiset=group1_traffic['gbn-local']==group1_traffic['sr-local'],
        local_same_data_drop_stream_sequence_size_multiset=fault_attempts['gbn-local']==fault_attempts['sr-local'],
        normal_group1_same_source_sequence_size_multiset=group1_traffic['gbn-normal']==group1_traffic['sr-normal'],
        scope='post-hoc observation of actual tails; controller is an uncalibrated reference; no causal partition or achievable gain claim')
    with output.open('x') as f:json.dump(record,f,indent=2);f.write('\n')
    print(json.dumps(dict(network_runs=0,same_local_group1_source_traffic=record['local_group1_same_source_sequence_size_multiset'],
        same_fault_attempts=record['local_same_data_drop_stream_sequence_size_multiset'],
        cases={name:dict(timeouts=c['timeout_recovery_requests'],pfc_pause=c['pfc_pause'],
            tails=[{k:v for k,v in t.items() if k not in ('cnp_received','source_tx_samples')} for t in c['tails']]) for name,c in cases.items()})))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();diagnose(a.root.resolve(),a.output.resolve())
