"""Offline event analysis. This program never opens truth or original routing files."""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, ensure_ascii=False, indent=2)+'\n')


def decode(result):
    capture=result['packet_capture']
    assert capture['complete'] and not capture['omitted_events']
    wire=capture['wire_headers']
    headers=[dict(zip(wire['columns'], row)) for row in wire['headers']]
    streams=result['persistent_transport']['streams']
    by_port={s['wire_port']:s['id'] for s in streams}
    by_ips={(0x0b000001+s['dst']*32, 0x0b000001+s['src']*32):s['id'] for s in streams}
    events=[]
    assert len(wire['event_header_indices'])==len(capture['events'])
    for row, hi in zip(capture['events'], wire['event_header_indices']):
        e=dict(zip(capture['columns'],row)); h=headers[hi]
        assert e['packet_uid']==h['packet_uid'] and e['ip_protocol']==h['ip_protocol']
        if h['ip_protocol'] in (17,252,253):
            sid=by_port[h['source_port']]
            assert h['source_port']==h['destination_port'] and sid==e['message_id']
        elif h['ip_protocol']==255:
            # Only one declared stream for each directed endpoint pair in this study.
            sid=by_ips[(h['ip_src'],h['ip_dst'])]
        else:
            sid=None
        e['stream']=sid
        events.append(e)
    return events


def analyze_case(result):
    events=decode(result); transport=result['persistent_transport']
    ports={(p['node'],p['port']):p for p in result['ports']}
    selective=transport.get('selective_repeat')
    received_state={e['packet_uid']:e for e in selective['events'] if e['kind']=='data_received'} if selective else {}
    streams={s['id']:s for s in transport['streams']}
    source_counts=Counter(); attempts={}; pending=defaultdict(deque); queue_records=[]
    # Reconstruct actual per-priority FIFO data queue contents, including retransmissions.
    live=defaultdict(list); receiver_prefix=Counter(); source_ack=Counter(); source_tx={}; ack_history=defaultdict(list)
    for e in events:
        sid=e['stream']; key=(e['node'],e['port'],e['packet_uid'],e['ip_protocol'],e['sequence'])
        if e['ip_protocol']==17 and e['event']==0 and e['node']==streams[sid]['src']:
            source_counts[sid,e['sequence']]+=1
            attempts[sid,e['packet_uid'],e['sequence']]=source_counts[sid,e['sequence']]
            source_tx[sid,e['packet_uid'],e['sequence']]=e['time_ns']
        if e['ip_protocol']==17 and e['event']==1 and e['node']==streams[sid]['dst']:
            if selective:receiver_prefix[sid]=received_state[e['packet_uid']]['receiver_prefix']
            elif e['sequence']==receiver_prefix[sid]:receiver_prefix[sid]+=e['data_payload_bytes']
        if e['ip_protocol'] in (252,253) and e['event']==1 and e['node']==streams[sid]['src']:
            source_ack[sid]=max(source_ack[sid],e['sequence'])
            ack_history[sid].append((e['time_ns'],e['sequence']))
        if e['event']==3:
            ahead=Counter(); repeated=Counter(); already_received=Counter(); already_acked=Counter(); examples=[]
            if e['ip_protocol']==17:
                for other in live[e['node'],e['port']]:
                    ahead[other['stream']]+=other['model_bytes']
                    if attempts[other['stream'],other['packet_uid'],other['sequence']]>1:
                        repeated[other['stream']]+=other['model_bytes']
                        end=other['sequence']+other['data_payload_bytes']
                        if end<=receiver_prefix[other['stream']]:already_received[other['stream']]+=other['model_bytes']
                        if end<=source_ack[other['stream']]:
                            already_acked[other['stream']]+=other['model_bytes']
                            if len(examples)<4:
                                examples.append(dict(stream=other['stream'],packet_uid=other['packet_uid'],sequence=other['sequence'],
                                    payload_bytes=other['data_payload_bytes'],model_bytes=other['model_bytes'],
                                    source_tx_ns=source_tx[other['stream'],other['packet_uid'],other['sequence']],
                                    owner_enqueue_ns=other['time_ns'],
                                    source_ack_ns=next(t for t,seq in ack_history[other['stream']] if seq>=end)))
                live[e['node'],e['port']].append(e)
            pending[key].append((e,dict(ahead),dict(repeated),dict(already_received),dict(already_acked),examples))
        elif e['event']==4:
            assert pending[key], ('dequeue_without_enqueue',key)
            enq,ahead,repeated,already_received,already_acked,examples=pending[key].popleft()
            if e['ip_protocol']==17:
                first=live[e['node'],e['port']].pop(0)
                assert (first['packet_uid'],first['sequence'])==(e['packet_uid'],e['sequence'])
            queue_records.append(dict(stream=sid, packet_uid=e['packet_uid'], sequence=e['sequence'],
                ip_protocol=e['ip_protocol'], node=e['node'], port=e['port'], peer=ports[e['node'],e['port']]['peer'],
                enqueue_ns=enq['time_ns'], dequeue_ns=e['time_ns'], wait_ns=e['time_ns']-enq['time_ns'],
                model_bytes=e['model_bytes'], data_bytes_ahead_by_stream=ahead, repeated_model_bytes_ahead_by_stream=repeated,
                repeated_already_received_before_enqueue=already_received,repeated_already_acked_before_enqueue=already_acked,
                stale_ahead_examples=examples))
    assert not any(pending.values()) and not any(live.values())
    rows=[]
    for sid,s in streams.items():
        es=[e for e in events if e['stream']==sid]
        work=sorted((w for w in transport['work'] if w['stream']==sid),key=lambda w:w['end'])
        tx=[e for e in es if e['event']==0 and e['ip_protocol']==17 and e['node']==s['src']]
        edge_sets={str(proto):sorted({(e['node'],ports[e['node'],e['port']]['peer']) for e in es
                                   if e['event']==0 and e['ip_protocol']==proto}) for proto in (17,252,253,255)}
        direct_edge=any((8,10) in edges or (10,8) in edges for edges in edge_sets.values())
        drops=Counter(str(e['ip_protocol']) for e in es if e['event'] in (2,5))
        ref=[e for e in transport['dcqcn_reference']['events'] if e['stream']==sid]
        received=[e['time_ns'] for e in ref if e['kind']=='cnp_received']
        last=work[-1]
        rx=[e for e in es if e['ip_protocol']==17 and e['event']==1 and e['node']==s['dst']
            and e['time_ns']==last['accepted_ns']]
        assert len(rx)==1, ('ambiguous_accept_witness',sid)
        witness=rx[0]
        # A witness may represent one retransmission lineage, not a globally unique UID.
        sources=[e for e in tx if e['packet_uid']==witness['packet_uid'] and e['sequence']==witness['sequence']
                 and e['time_ns']<=witness['time_ns']]
        assert len(sources)==1, ('ambiguous_source_witness',sid)
        source=sources[0]
        qs=[q for q in queue_records if q['stream']==sid and q['ip_protocol']==17
            and q['packet_uid']==witness['packet_uid'] and q['sequence']==witness['sequence']
            and source['time_ns']<=q['enqueue_ns']<=q['dequeue_ns']<=witness['time_ns']]
        qs.sort(key=lambda q:q['enqueue_ns'])
        rows.append(dict(stream=sid,src=s['src'],dst=s['dst'],work=len(work),unique_bytes=s['submitted_bytes'],
            tx_payload_bytes=sum(e['data_payload_bytes'] for e in tx),source_packets=len(tx),
            repeated_payload_bytes=sum(e['data_payload_bytes'] for e in tx)-s['submitted_bytes'],
            dropped_by_protocol=dict(drops),direct_edge_seen=direct_edge,edges=edge_sets,
            accepted_ns=last['accepted_ns'],acked_ns=last['acked_ns'],last_source_tx_ns=max(e['time_ns'] for e in tx),
            first_cnp_ns=min(received) if received else None,cnp_received=len(received),
            witness=dict(sequence=witness['sequence'],payload_bytes=witness['data_payload_bytes'],packet_uid=witness['packet_uid'],
                source_tx_ns=source['time_ns'],destination_rx_ns=witness['time_ns'],
                source_to_accept_ns=witness['time_ns']-source['time_ns'],queue_wait_ns=sum(q['wait_ns'] for q in qs),
                nonqueue_transit_ns=witness['time_ns']-source['time_ns']-sum(q['wait_ns'] for q in qs),queues=qs)))
    return dict(finish_ns=result['finish_ns'],all_work_ack_ns=max(w['acked_ns'] for w in transport['work']),
        all_work_accepted_ns=max(w['accepted_ns'] for w in transport['work']),
        last_acked_streams=sorted({w['stream'] for w in transport['work'] if w['acked_ns']==max(x['acked_ns'] for x in transport['work'])}),
        last_accepted_streams=sorted({w['stream'] for w in transport['work'] if w['accepted_ns']==max(x['accepted_ns'] for x in transport['work'])}),
        source_model_bytes_by_protocol=dict(Counter({str(proto):sum(e['model_bytes'] for e in events if e['event']==0 and e['ip_protocol']==proto and e['node']<result['gpu_count']) for proto in (17,252,253,255)})),
        sum_per_stream_peak_reorder_bytes=sum(s['peak_reorder_bytes'] for s in selective['states']) if selective else 0,
        unique_bytes=sum(s['submitted_bytes'] for s in streams.values()),
        tx_payload_bytes=sum(row['tx_payload_bytes'] for row in rows),
        packet_capture_events=len(events),physical_tx=sum(e['event']==0 for e in events),
        physical_rx=sum(e['event']==1 for e in events),physical_drop=sum(e['event']==2 for e in events),
        local_drop=sum(e['event']==5 for e in events),
        pfc_pause_callbacks=sum(p['pfc_pause'] for p in result['ports']),
        pfc_resume_callbacks=sum(p['pfc_resume'] for p in result['ports']),
        timeouts=sum(s['timeout_retransmissions'] for s in transport['recovery']['states']),streams=rows)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--observed',type=Path,required=True)
    p.add_argument('--runs',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); assert not a.output.exists()
    cases={c['name']:analyze_case(load(a.runs/c['name']/'result.json')) for c in load(a.observed/'index.json')['cases']}
    comparisons=[]
    for timer in ('gbn','selective'):
        base={s['stream']:s for s in cases[f'healthy-{timer}']['streams']}
        for s in cases[f'failed-{timer}']['streams']:
            b=base[s['stream']]
            category=('direct_drop' if s['dropped_by_protocol'] else
                      'baseline_uses_failed_edge' if b['direct_edge_seen'] else
                      'no_drop_off_failed_edge_changed_paths' if s['edges']!=b['edges'] else
                      'no_drop_off_failed_edge_same_paths')
            comparisons.append(dict(timer=timer,stream=s['stream'],category=category,
                ack_delta_ns=s['acked_ns']-b['acked_ns'],accepted_delta_ns=s['accepted_ns']-b['accepted_ns'],
                extra_tx_payload_bytes=s['tx_payload_bytes']-b['tx_payload_bytes'],
                witness_same_offset=s['witness']['sequence']==b['witness']['sequence'],
                witness_source_tx_delta_ns=s['witness']['source_tx_ns']-b['witness']['source_tx_ns'],
                witness_queue_delta_ns=s['witness']['queue_wait_ns']-b['witness']['queue_wait_ns'],
                witness_nonqueue_delta_ns=s['witness']['nonqueue_transit_ns']-b['witness']['nonqueue_transit_ns']))
    save(a.output,dict(schema_version='selective-externality-analysis/1',cases=cases,comparisons=comparisons,
        mode_effect={net:dict(ack_delta_ns=cases[f'{net}-selective']['all_work_ack_ns']-cases[f'{net}-gbn']['all_work_ack_ns'],
                            accepted_delta_ns=cases[f'{net}-selective']['all_work_accepted_ns']-cases[f'{net}-gbn']['all_work_accepted_ns'],
                            payload_delta_bytes=cases[f'{net}-selective']['tx_payload_bytes']-cases[f'{net}-gbn']['tx_payload_bytes']) for net in ('healthy','failed')},
        limitations=['Large nonbinding window eliminates an active sending-cap change; reorder state and 2B extra feedback model cost remain.',
                    'Small single-layer cold Dispatch window, not complete inference or independent heldout.',
                    'Per-packet queue residence explains its transit, not counterfactual removable business loss.',
                    'Same edge sets do not alone guarantee identical per-packet paths; inspect the witness.',
                    'Control-model drain time differs from business ACK completion.',
                    'No calibrated GPU/NIC, production control plane or thousand-GPU claim.']))
    print(json.dumps(dict(cases=len(cases),comparisons=len(comparisons),output=str(a.output))))


if __name__=='__main__': main()
