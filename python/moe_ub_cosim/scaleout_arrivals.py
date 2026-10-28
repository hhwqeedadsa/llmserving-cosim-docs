"""Endpoint accounting and accepted-byte curves for saved rdma-run GBN services.

Evaluation only. MacRx precedes protocol acceptance: replay the locked receiver's
exact-next-sequence rule, and cross-check its final state and data-ready callback.
No packet UID deduplication: UID denotes lineage, not a transmission identity.
"""
from collections import Counter
import csv
import json
from pathlib import Path

from .scaleout_topology import require


def add_range(ranges, start, end):
    """Insert a byte interval, returning newly covered bytes (not packet count)."""
    before = sum(b-a for a,b in ranges)
    merged = []
    for a,b in sorted(ranges+[(start,end)]):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b,merged[-1][1]))
        else:
            merged.append((a,b))
    ranges[:] = merged
    return sum(b-a for a,b in ranges)-before


def reconstruct(result, waves, graph, layer, phase, src=None, dst=None):
    cap = result.get('packet_capture', {})
    require(result.get('native_network_executed') and 'service' in result,
            'requires a native rdma-run service result, not a stream/transaction probe')
    require(cap.get('complete') is True and cap.get('omitted_events') == 0,
            'complete packet capture required; truncated data cannot reconstruct arrivals')
    sel = cap['selection']
    require(sel['start_ns'] == 0 and sel['end_ns'] > result['finish_ns'] and not sel['nodes'],
            'capture must cover every node from zero through simulation finish')
    codes = cap['event_codes']; protos = cap['protocol_codes']
    require(protos['data'] == 17 and codes['rx'] == 1 and codes['tx'] == 0,
            'unsupported packet-capture semantics')
    hosts=result.get('rdma_parameters',{}).get('hosts',[])
    require(len(hosts)==result['gpu_count'] and {h['node'] for h in hosts}==set(range(result['gpu_count']))
            and all(h['attributes'].get('L2BackToZero') is False for h in hosts),
            'recorded receiver parameters must confirm L2BackToZero=false for every GPU')
    metadata, source_layers, selected = {}, {}, set()
    for wave in waves:
        for row in wave['routes']:
            source_layers[wave['graph_id'],row['layer']] = row.get('source_layer_id')
        for msg in wave['messages']:
            identity = msg['id']
            require(identity not in metadata, 'repeated message identity across graphs')
            metadata[identity] = dict(msg, graph_id=wave['graph_id'], group_index=wave['group_index'],
                                      decode_step=wave['decode_step'])
            if (wave['graph_id'] == graph and msg['layer'] == layer and msg['phase'] == phase
                    and (src is None or msg['src'] == src) and (dst is None or msg['dst'] == dst)):
                selected.add(identity)
    require(selected, 'collective slice contains no messages in the generated graphs')
    actual = {m['id']:m for m in result['messages']}
    require(len(actual) == len(result['messages']) and set(actual) <= set(metadata),
            'service messages cannot be joined to saved wave graphs')
    require(all(m.get('transport') != 'intra_host_chunked_crossbar' for m in actual.values()),
            'local crossbar has no packet capture; use a packet-network-only service')
    stats, ranges_tx, ranges_rx, accepted, confirmed = {}, {}, {}, {}, {}
    for identity, meta in metadata.items():
        m = actual.get(identity)
        if m:
            require(all(m[k] == meta[k] for k in ('src','dst','bytes','plane')), 'graph/result mismatch')
        stats[identity] = dict(message_id=identity, graph_id=meta['graph_id'], layer=meta['layer'],
            source_layer_id=source_layers[meta['graph_id'],meta['layer']], phase=meta['phase'],
            src=meta['src'], dst=meta['dst'], logical_bytes=meta['bytes'], submitted=m is not None,
            slice_selected=identity in selected, tx_packets=0, tx_payload_bytes=0, tx_model_bytes=0,
            tx_unique_bytes=0, rx_packets=0, rx_payload_bytes=0, rx_model_bytes=0,
            rx_unique_bytes=0, accepted_bytes=0, duplicate_rx_packets=0,
            out_of_order_discard_packets=0, out_of_order_discard_bytes=0,
            data_drop_events=0, dropped_payload_bytes=0,
            first_tx_ns=None, first_rx_ns=None, data_ready_ns=None, ack_ns=m.get('ack_ns') if m else None)
        ranges_tx[identity], ranges_rx[identity] = [], []
        accepted[identity], confirmed[identity] = 0, 0
    endpoints = {r:Counter(rank=r) for r in range(result['gpu_count'])}
    packets, curve = [], [dict(time_ns=0, tx_payload_bytes=0, rx_payload_bytes=0,
                               unique_arrival_bytes=0, accepted_bytes=0, confirmed_bytes=0)]
    totals = dict(curve[0]); del totals['time_ns']
    columns = cap['columns']; last_time = -1
    for index, values in enumerate(cap['events']):
        require(len(values) == len(columns), 'packet row width differs from columns')
        p = dict(zip(columns, values)); t=p['time_ns']; event=p['event']; protocol=p['ip_protocol']
        require(last_time <= t <= result['finish_ns'], 'packet events must preserve simulation order')
        last_time=t; identity=p['message_id']; node=p['node']
        if node in endpoints and event in (codes['tx'],codes['rx']):
            direction = 'tx' if event == codes['tx'] else 'rx'
            kind = 'data' if protocol == protos['data'] else 'control'
            endpoints[node][direction+'_'+kind+'_model_bytes'] += p['model_bytes']
            endpoints[node][direction+'_'+kind+'_packets'] += 1
        if identity not in metadata:
            continue  # CNP/PFC can have no application association.
        meta=metadata[identity]; row=stats[identity]; payload=p['data_payload_bytes']; seq=p['sequence']
        delta=0; role='network'; old_totals=dict(totals)
        if protocol == protos['data']:
            require(payload > 0 and 0 <= seq < seq+payload <= meta['bytes'], 'invalid payload byte range')
            if event == codes['tx'] and node == meta['src']:
                role='source_tx'; row['tx_packets']+=1; row['tx_payload_bytes']+=payload
                row['tx_model_bytes']+=p['model_bytes']
                row['tx_unique_bytes']+=add_range(ranges_tx[identity],seq,seq+payload)
                if row['first_tx_ns'] is None:row['first_tx_ns']=t
                if identity in selected:totals['tx_payload_bytes']+=payload
            elif event == codes['rx'] and node == meta['dst']:
                role='destination_rx'; row['rx_packets']+=1; row['rx_payload_bytes']+=payload
                row['rx_model_bytes']+=p['model_bytes']
                unique=add_range(ranges_rx[identity],seq,seq+payload); row['rx_unique_bytes']+=unique
                if row['first_rx_ns'] is None:row['first_rx_ns']=t
                if seq == accepted[identity]:
                    delta=payload; accepted[identity]+=delta; role='accepted_in_order'
                    if accepted[identity] == meta['bytes']:row['data_ready_ns']=t
                elif seq < accepted[identity]:
                    role='duplicate_discard';row['duplicate_rx_packets']+=1
                else:
                    role='out_of_order_discard';row['out_of_order_discard_packets']+=1
                    row['out_of_order_discard_bytes']+=payload
                if identity in selected:
                    totals['rx_payload_bytes']+=payload;totals['unique_arrival_bytes']+=unique
                    totals['accepted_bytes']+=delta
            elif event in (codes['drop'], codes.get('local_drop', -999)):
                role='data_drop';row['data_drop_events']+=1;row['dropped_payload_bytes']+=payload
        elif protocol in (protos['ack'],protos['nack']) and event == codes['rx'] and node == meta['src']:
            role='source_feedback';require(0 <= seq <= meta['bytes'], 'feedback outside message')
            progress=max(confirmed[identity],seq)-confirmed[identity];confirmed[identity]+=progress
            if identity in selected:totals['confirmed_bytes']+=progress
        if identity in selected:
            packets.append(dict(capture_index=index, **p, graph_id=meta['graph_id'], layer=meta['layer'],
                phase=meta['phase'], src=meta['src'], dst=meta['dst'], role=role,
                accepted_delta_bytes=delta, message_accepted_bytes=accepted[identity]))
            if old_totals != totals:curve.append(dict(time_ns=t,**totals))
    for identity,row in stats.items():
        row['accepted_bytes']=accepted[identity]
        row['tx_duplicate_payload_bytes']=row['tx_payload_bytes']-row['tx_unique_bytes']
        row['rx_duplicate_payload_bytes']=row['rx_payload_bytes']-row['rx_unique_bytes']
        m=actual.get(identity)
        if m:
            require(row['tx_packets'] == m['source_data_packets'] and row['tx_payload_bytes'] == m['source_data_payload_bytes'],
                    'source packet accounting differs from native source counters')
            require(accepted[identity] == m['receiver_contiguous_bytes'], 'accepted curve differs from receiver state')
            require(row['data_ready_ns'] == m['receive_ready_ns'], 'accepted curve differs from actual data-ready callback')
            if m.get('ack_ns') is not None:require(confirmed[identity] == m['bytes'], 'complete source missing full feedback')
        endpoints[row['src']]['logical_send_bytes']+=row['logical_bytes']
        endpoints[row['dst']]['logical_receive_bytes']+=row['logical_bytes']
        endpoints[row['src']]['source_tx_payload_bytes']+=row['tx_payload_bytes']
        endpoints[row['dst']]['destination_rx_payload_bytes']+=row['rx_payload_bytes']
        endpoints[row['dst']]['accepted_bytes']+=row['accepted_bytes']
    curve.append(dict(time_ns=result['finish_ns'],**totals))
    chosen=[row for row in stats.values() if row['slice_selected']]
    target=sum(row['logical_bytes'] for row in chosen)
    summary=dict(schema_version='moe-rdma-arrival-audit/1', simulation_finished_ns=result['finish_ns'],
        service_passed=result['passed'], capture_complete=True, gpu_count=result['gpu_count'],
        generated_graphs=len(waves), generated_graph_message_count=len(metadata), submitted_message_count=len(actual),
        generated_graph_logical_bytes=sum(m['bytes'] for m in metadata.values()),
        submitted_logical_bytes=sum(m['bytes'] for m in actual.values()),
        source_tx_payload_bytes=sum(r['tx_payload_bytes'] for r in stats.values()),
        destination_rx_payload_bytes=sum(r['rx_payload_bytes'] for r in stats.values()),
        accepted_bytes=sum(accepted.values()),
        slice=dict(graph_id=graph,layer=layer,source_layer_id=source_layers.get((graph,layer)),phase=phase,
            src=src,dst=dst,messages=len(chosen),logical_bytes=target,**totals,
            complete=totals['accepted_bytes']==target,
            data_ready_ns=max(r['data_ready_ns'] for r in chosen) if all(r['data_ready_ns'] is not None for r in chosen) else None,
            source_confirmed_ns=max(r['ack_ns'] for r in chosen) if all(r['ack_ns'] is not None for r in chosen) else None),
        semantics=dict(receiver='locked GBN exact-next byte sequence; L2BackToZero=false',
            accepted='sum of contiguous protocol-accepted bytes per message; excludes duplicates and discarded out-of-order data',
            unique_arrival='byte-range union of physical destination arrivals; may include discarded out-of-order data',
            timestamps='simulated ns; MacRx before receiver acceptance at the same ns; not GPU-visible timestamps',
            model_bytes='native packet size including modeled headers; excludes unmodeled Ethernet preamble/FCS/IFG',
            scope='generated graphs only if failed; future ungenerated waves are not included; evaluation-only association'))
    return summary, list(stats.values()), [dict(v) for v in endpoints.values()], packets, curve


def execute(run, output, graph='g0.w0', layer=0, phase='dispatch', src=None, dst=None):
    run,output=Path(run).resolve(),Path(output).resolve()
    require(not output.exists(), 'arrival output directory must not exist')
    result_path=run/'result.json'
    require(result_path.is_file() and result_path.stat().st_size <= 64*2**20, 'missing result or exceeds 64MiB audit limit')
    paths=sorted((run/'graphs').glob('*/wave.json'))
    require(0 < len(paths) <= 1024 and sum(p.stat().st_size for p in paths) <= 64*2**20, 'wave audit budget exceeded')
    result=json.loads(result_path.read_bytes());waves=[json.loads(p.read_bytes()) for p in paths]
    require(len(result.get('packet_capture',{}).get('events',[])) <= 100000, 'packet audit limit exceeded')
    summary,messages,endpoints,packets,curve=reconstruct(result,waves,graph,layer,phase,src,dst)
    output.mkdir(parents=True)
    for name,rows in (('messages',messages),('endpoints',endpoints),('packet_events',packets),('arrival_curve',curve)):
        keys=list(dict.fromkeys(k for row in rows for k in row))
        with (output/(name+'.csv')).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader();writer.writerows(rows)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary
