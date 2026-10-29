"""Evaluate actual ASTRA messages on persistent connection byte intervals.

Packet ``message_id`` is a STREAM id here. Never relabel wire headers or feed
these evaluation associations to the runtime. Receiver acceptance is replayed
from destination packets with the declared finite reordering capacity.
"""
from collections import Counter, defaultdict

from .scaleout_topology import require
from .scaleout_arrivals import add_range


def reconstruct(result, waves, graph, layer, phase, src=None, dst=None):
    cap=result.get('packet_capture',{})
    require(cap.get('complete') is True and cap.get('omitted_events')==0,
            'persistent service requires complete packet capture')
    selection=cap['selection']
    require(cap['event_codes']['tx']==0 and cap['event_codes']['rx']==1 and cap['protocol_codes']['data']==17,
            'unsupported persistent packet capture semantics')
    require(selection['start_ns']==0 and selection['end_ns']>result['finish_ns'] and not selection['nodes'],
            'persistent capture must cover all endpoints through finish')
    transport=result['service_transport'];native=transport['native']
    require(transport['kind']=='astra_persistent_streams/1' and not result['messages'],
            'persistent service cannot mix legacy message accounting')
    config=native['recovery']['config'];selective=config['mode']=='selective_repeat/1'
    require(config['mode'] in ('selective_repeat/1','go_back_n/1'),'unknown stream acceptance')
    streams={s['id']:s for s in native['streams']}
    work={w['id']:w for w in native['work']}
    actual={m['id']:m for m in transport['messages']}
    require(len(streams)==len(native['streams']) and len(work)==len(native['work'])==len(actual)==len(transport['messages'])
            and set(work)==set(actual),'duplicate or missing stream/work/message')
    metadata={};sources={};chosen=set()
    for wave in waves:
        for row in wave['routes']:sources[wave['graph_id'],row['layer']]=row.get('source_layer_id')
        for m in wave['messages']:
            identity=m['id'];require(identity not in metadata,'duplicate generated message')
            metadata[identity]=dict(m,graph_id=wave['graph_id'],group_index=wave['group_index'],decode_step=wave['decode_step'])
            if wave['graph_id']==graph and m['layer']==layer and m['phase']==phase and (
                    src is None or m['src']==src) and (dst is None or m['dst']==dst):chosen.add(identity)
    require(chosen and set(actual)<=set(metadata),'missing collective or extra submitted message')
    rows={};ranges_tx=defaultdict(list);ranges_rx=defaultdict(list);by_stream=defaultdict(list)
    for identity,m in metadata.items():
        rows[identity]=dict(message_id=identity,graph_id=m['graph_id'],group_index=m['group_index'],
            decode_step=m['decode_step'],layer=m['layer'],phase=m['phase'],src=m['src'],dst=m['dst'],
            plane=m['plane'],logical_bytes=m['bytes'],submitted=identity in actual,slice_selected=identity in chosen,
            tx_packets=0,tx_payload_bytes=0,tx_unique_bytes=0,rx_packets=0,rx_payload_bytes=0,rx_unique_bytes=0,
            accepted_bytes=0,confirmed_bytes=0,data_ready_ns=None,ack_ns=None)
        rows[identity].update(stream_id=None,stream_begin=None,stream_end=None)
        if identity in actual:
            a,w=actual[identity],work[identity]
            rows[identity].update(stream_id=a['stream'],stream_begin=a['begin'],stream_end=a['end'])
            require(all(a[k]==m[k] for k in ('src','dst','plane','bytes')) and
                    all(a[k]==w[k] for k in ('stream','bytes','begin','end','ready_ns')) and
                    a['published_ns']==(None if w['published_ns']<0 else w['published_ns']),
                    'message/work interval or identity differs')
            require(a['stream'] in streams,'unknown message stream')
            s=streams[a['stream']]
            require(all(a[k]==s[k] for k in ('src','dst','plane')),'stream endpoint differs from message')
            if w['published_ns']>=0:
                require(w['end']-w['begin']==m['bytes'] and 0<=w['ready_ns']<=w['published_ns'],
                        'invalid published byte interval')
                by_stream[a['stream']].append(identity)
    for sid,ids in by_stream.items():
        ids.sort(key=lambda i:actual[i]['begin'])
        cursor=0
        for identity in ids:
            m=actual[identity];require(m['begin']==cursor,'overlapping or discontinuous stream work')
            cursor=m['end']
        require(cursor==streams[sid]['published_bytes'],'published stream prefix differs from message intervals')
    prefixes=Counter();confirmed=Counter();held=defaultdict(dict);used=Counter();source_packets=Counter();source_bytes=Counter()
    totals=dict(tx_payload_bytes=0,unique_arrival_bytes=0,accepted_bytes=0,source_confirmed_bytes=0)
    curve=[dict(time_ns=0,**totals)];packets=[]
    previous=-1
    for raw in cap['events']:
        packet=dict(zip(cap['columns'],raw));time=packet['time_ns']
        require(previous<=time<=result['finish_ns'],'packet time order violated');previous=time
        sid=packet.pop('message_id');packet['stream_id']=sid
        packet['semantic_message_ids']='';packets.append(packet)
        if sid not in streams:continue  # PFC/CNP may have no stream association.
        s=streams[sid];event=packet['event'];protocol=packet['ip_protocol'];node=packet['node'];seq=packet['sequence']
        before_totals=tuple(totals.values())
        if protocol==17:
            size=packet['data_payload_bytes'];end=seq+size
            ids=[i for i in by_stream[sid] if actual[i]['begin']<=seq<end<=actual[i]['end']]
            require(len(ids)==1 and size>0,'data packet crosses/unpublished message interval')
            identity=ids[0];a=actual[identity];row=rows[identity];packet['semantic_message_ids']=str(identity)
            require(time>=a['published_ns'],'packet precedes message publication')
            if node==s['src'] and event==0:
                require(packet['port']==s['source_nic'],'source packet used wrong physical NIC')
                row['tx_packets']+=1;row['tx_payload_bytes']+=size
                row['tx_unique_bytes']+=add_range(ranges_tx[identity],seq,end)
                source_packets[sid]+=1;source_bytes[sid]+=size
                if identity in chosen:totals['tx_payload_bytes']+=size
            if node==s['dst'] and event==1:
                require(packet['port']==s['destination_nic'],'destination packet used wrong physical NIC')
                row['rx_packets']+=1;row['rx_payload_bytes']+=size
                unique=add_range(ranges_rx[identity],seq,end);row['rx_unique_bytes']+=unique
                if identity in chosen:totals['unique_arrival_bytes']+=unique
                before=prefixes[sid]
                if selective:
                    if end<=before:pass  # A complete duplicate may elicit another ACK.
                    elif seq<before:raise ValueError('partially overlapping selective packet')
                    elif seq in held[sid]:require(held[sid][seq]==end,'changed buffered packet boundary')
                    elif seq==before:
                        prefixes[sid]=end
                        while prefixes[sid] in held[sid]:
                            start=prefixes[sid];finish=held[sid].pop(start)
                            used[sid]-=finish-start;prefixes[sid]=finish
                    else:
                        require(all(end<=a or seq>=b for a,b in held[sid].items()),'overlapping reorder packets')
                        if used[sid]+size<=config['receive_buffer_bytes'] and len(held[sid])<4096:
                            held[sid][seq]=end;used[sid]+=size
                elif seq==before:prefixes[sid]=end
                for identity in by_stream[sid]:
                    a=actual[identity];row=rows[identity]
                    value=max(0,min(prefixes[sid],a['end'])-a['begin']);delta=value-row['accepted_bytes']
                    row['accepted_bytes']=value
                    if value==row['logical_bytes'] and row['data_ready_ns'] is None:row['data_ready_ns']=time
                    if identity in chosen:totals['accepted_bytes']+=delta
        elif protocol in (252,253) and node==s['src'] and event==1:
            require(packet['port']==s['source_nic'] and 0<=seq<=prefixes[sid],
                    'feedback confirms bytes not accepted or uses wrong NIC')
            confirmed[sid]=max(confirmed[sid],seq)
            affected=[]
            for identity in by_stream[sid]:
                a=actual[identity];row=rows[identity]
                value=max(0,min(confirmed[sid],a['end'])-a['begin']);delta=value-row['confirmed_bytes']
                row['confirmed_bytes']=value
                if delta:affected.append(str(identity))
                if value==row['logical_bytes'] and row['ack_ns'] is None:row['ack_ns']=time
                if identity in chosen:totals['source_confirmed_bytes']+=delta
            packet['semantic_message_ids']=','.join(affected)
        if tuple(totals.values())!=before_totals:curve.append(dict(time_ns=time,**totals))
    for sid,s in streams.items():
        require(source_packets[sid]==s['source_data_packets'] and source_bytes[sid]==s['source_payload_bytes'],
                'independent stream source packet accounting differs')
        require(prefixes[sid]==s['published_bytes'] if result['passed'] else prefixes[sid]<=s['published_bytes'],
                'receiver prefix outside published stream')
        require(confirmed[sid]==s['snd_una'],'source cumulative confirmation differs')
    endpoints={rank:dict(rank_id=rank,logical_send_bytes=0,logical_receive_bytes=0,source_tx_payload_bytes=0,
        destination_rx_payload_bytes=0,accepted_bytes=0) for rank in range(result['gpu_count'])}
    for identity,row in rows.items():
        if identity in actual:
            a=actual[identity];w=work[identity]
            require(row['data_ready_ns']==a['receive_ready_ns']==(None if w['accepted_ns']<0 else w['accepted_ns']),
                    'receiver completion differs from independent packet prefix')
            require(row['ack_ns']==a['ack_ns']==(None if w['acked_ns']<0 else w['acked_ns']),
                    'source completion differs from independent packet feedback')
            if result['passed']:require(row['tx_unique_bytes']==row['accepted_bytes']==row['logical_bytes'],
                                       'successful message does not conserve useful bytes')
        row['tx_duplicate_payload_bytes']=row['tx_payload_bytes']-row['tx_unique_bytes']
        row['rx_duplicate_payload_bytes']=row['rx_payload_bytes']-row['rx_unique_bytes']
        endpoints[row['src']]['logical_send_bytes']+=row['logical_bytes']
        endpoints[row['dst']]['logical_receive_bytes']+=row['logical_bytes']
        endpoints[row['src']]['source_tx_payload_bytes']+=row['tx_payload_bytes']
        endpoints[row['dst']]['destination_rx_payload_bytes']+=row['rx_payload_bytes']
        endpoints[row['dst']]['accepted_bytes']+=row['accepted_bytes']
    selected=[rows[i] for i in chosen];target=sum(row['logical_bytes'] for row in selected)
    curve.append(dict(time_ns=result['finish_ns'],**totals))
    summary=dict(schema_version='moe-rdma-arrival-audit/2',transport_kind='astra_persistent_streams/1',
        simulation_finished_ns=result['finish_ns'],
        service_passed=result['passed'],capture_complete=True,gpu_count=result['gpu_count'],generated_graphs=len(waves),
        generated_graph_message_count=len(metadata),submitted_message_count=len(actual),
        generated_graph_logical_bytes=sum(m['bytes'] for m in metadata.values()),
        submitted_logical_bytes=sum(m['bytes'] for m in actual.values()),
        source_tx_payload_bytes=sum(r['tx_payload_bytes'] for r in rows.values()),
        destination_rx_payload_bytes=sum(r['rx_payload_bytes'] for r in rows.values()),
        accepted_bytes=sum(r['accepted_bytes'] for r in rows.values()),
        slice=dict(graph_id=graph,layer=layer,source_layer_id=sources.get((graph,layer)),phase=phase,src=src,dst=dst,
            messages=len(selected),logical_bytes=target,**totals,complete=totals['accepted_bytes']==target,
            data_ready_ns=max(r['data_ready_ns'] for r in selected) if all(r['data_ready_ns'] is not None for r in selected) else None,
            source_confirmed_ns=max(r['ack_ns'] for r in selected) if all(r['ack_ns'] is not None for r in selected) else None),
        semantics=dict(receiver=config['mode'],identity='original packets identify streams; message association uses published byte intervals',
            packet_export='full captured network; semantic_message_ids is an evaluation association, not a wire field',
            accepted='contiguous accepted byte prefixes; selective mode independently replays finite reordering capacity',
            timestamps='simulated ns, not GPU visibility or calibrated hardware',scope='generated graphs only; evaluation association'))
    return summary,list(rows.values()),list(endpoints.values()),packets,curve
