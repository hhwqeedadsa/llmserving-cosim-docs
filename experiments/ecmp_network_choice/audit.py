"""Shared-uplink accounting extended to actual ECMP data/ACK/CNP paths."""
from collections import Counter, defaultdict, deque
from experiments.compact_network_choice.compact import stage_of
from experiments.shared_uplink_choice.prepare import load
from experiments.ecmp_baseline_audit.verify import all_paths, hash_bytes


def need(condition, message):
    if not condition: raise ValueError(message)


def enumerate_path(topology, src, dst, snic, dnic, sip, dip, sport, dport, seed):
    """Enumerate simple shortest paths, then independently apply native byte hash."""
    candidates=all_paths(topology,src,snic,dst,dnic)
    key=b''.join(value.to_bytes(4,'little') for value in (sip,dip,sport | dport << 16))
    answer=[candidates[0][0]]
    for position in range(1,len(candidates[0])):
        node=candidates[0][position][0]
        ports=list(dict.fromkeys(path[position][1] for path in candidates))
        chosen=ports[hash_bytes(key,seed+node)%len(ports)]
        answer.append((node,chosen))
        candidates=[path for path in candidates if path[position]==(node,chosen)]
    need(len(candidates)==1,'ambiguous selected path')
    return answer


def inspect(directory, prediction, result=None):
    topology=load(directory/'topology.json')
    initial=load(directory/'initial.json')
    execution=load(directory/'modern-execution.json')
    plan=load(directory/'modern-plan.json')
    result=load(directory/'result.json') if result is None else result
    protocol=load(directory/'protocol.json')
    cap=result['packet_capture']
    need(result['passed'] and result['drained'] and cap['complete'] and cap['omitted_events']==0,'incomplete result or capture')
    need(result['session']['finished'] and result['transactions']['transport']['physical_drained'],'not drained')
    need(execution['runtime']['all_events_fired'] and not execution['runtime']['memory_state']['active_source_leases'],'unfinished application or source lease')
    need(all(0<=e['time_ns']<=result['finish_ns'] for e in execution['events']),'application outside simulation')
    need(all(e['time_ns']==e['native_time_ns'] for e in execution['events'] if 'native_time_ns' in e),'callback time changed')
    operations={o['id']:o for o in plan['operations']}
    connections={c['id']:c for c in initial['connections']}
    node_to_rank={r['node']:r['rank'] for r in load(directory/'input.json')['deployment']}
    cells=Counter()
    for native in execution['native_operations']:
        task=execution['native_tasks'][str(native['id'])]
        if task['role']!='operation' or task['path']!='rdma':continue
        op=operations[task['modern_operation']]
        conn=connections[native['connection']]
        need(node_to_rank[conn['src']]==op['src'] and node_to_rank[conn['dst']]==op['dst'],'wrong native endpoints')
        n=8 if native['verb']=='fetch_add' else native['source']['bytes'] if 'source' in native else len(bytes.fromhex(native['hex']))
        need(n==op['operand_bytes'],'actual operand differs from plan')
        key=(stage_of(op),op['src'],op['dst'],conn['source_nic'],conn['destination_nic'],'request')
        cells[key]+=32+n
        if native['verb']=='fetch_add':
            s,a,b,an,bn,d=key
            cells[s,b,a,bn,an,'reply']+=40
    expected=Counter({(r['stage'],r['src'],r['dst'],r['source_nic'],r['destination_nic'],r['direction']):r['bytes']
                      for r in prediction['messages']})
    need(cells==expected,'symbolic slot-multiset load differs from actual operation bindings')
    links={}
    for link in topology['links']:
        for a,b,ap,bp in ((link['src'],link['dst'],link['src_port'],link['dst_port']),
                          (link['dst'],link['src'],link['dst_port'],link['src_port'])):
            links[a,ap]=(b,bp,link['rate_bps'],link['delay_ns'])
    kinds={n['id']:n['kind'] for n in topology['nodes']}
    addresses={r['ipv4']:(r['gpu'],r['nic']) for r in result['physical_nic_addressing']['bindings']}
    headers=cap['wire_headers']['headers']
    indices=cap['wire_headers']['event_header_indices']
    need(len(cap['events'])==len(indices)==cap['observations'],'inconsistent capture length')
    transport=result['transactions']['transport']
    streams={s['id']:s for s in transport['streams']}
    cnp_streams={e['packet_uid']:e['stream'] for e in transport['dcqcn_reference']['events'] if e['kind']=='cnp_generated'}
    tagged=[]
    packets=defaultdict(list)
    for row,index in zip(cap['events'],indices):
        header=headers[index]
        need(header[0]==row[4] and header[3]==row[6],'header/event identity mismatch')
        need(row[3] in (0,1,3,4),'unexpected packet drop')
        key=tuple(header)+(row[7],row[8],row[11])
        tagged.append((row,key))
        packets[key].append(row)
    tx_count,rx_count,data_bytes,control_bytes,payload_bytes=Counter(),Counter(),Counter(),Counter(),Counter()
    transmissions=defaultdict(list)
    paths_checked=0
    for key,rows in packets.items():
        tx=sorted([r for r in rows if r[3]==0],key=lambda r:r[0])
        rx=[r for r in rows if r[3]==1]
        need(len(tx)==len(rx) and tx,'missing TX/RX')
        if key[3] in (17,252,253,255):
            need(key[1] in addresses and key[2] in addresses,'unknown endpoint address')
            src,snic=addresses[key[1]]
            dst,dnic=addresses[key[2]]
            sport,dport=key[4:6]
            if key[3]==255:
                need(key[0] in cnp_streams,'CNP missing generation identity')
                sport=dport=streams[cnp_streams[key[0]]]['wire_port']
            expected_path=enumerate_path(topology,src,dst,snic,dnic,key[1],key[2],sport,dport,protocol['seed'])
            actual_path=[(r[1],r[2]) for r in tx]
            need(actual_path==expected_path,'wrong physical NIC or shortest path')
            paths_checked+=1
        previous=None
        for row in tx:
            node,port=row[1:3]
            peer,peer_port,rate,delay=links[node,port]
            matching=[r for r in rx if r[1]==peer and r[2]==peer_port]
            need(len(matching)==1,'missing/ambiguous physical receive')
            received=matching[0]
            duration=(row[8]*8*10**9+rate//2)//rate
            need(received[0]==row[0]+duration+delay,'serialization or propagation mismatch')
            if previous is not None:need(row[0]>=previous,'forward before receive')
            previous=received[0]
            tx_count[node,port]+=1
            rx_count[peer,peer_port]+=1
            if row[6]==17:
                data_bytes[node,port]+=row[8]
                payload_bytes[node,port]+=row[11]
            else:control_bytes[node,port]+=row[8]
            transmissions[node,port].append((row[0],row[0]+duration,key,row[6],row[8]))
    need(sum(tx_count.values())==sum(r[3]==0 for r,k in tagged),'unaccounted transmission')
    for intervals in transmissions.values():
        intervals.sort()
        need(all(a[1]<=b[0] for a,b in zip(intervals,intervals[1:])),'overlapping physical serializer')
    load_bytes=Counter()
    for row in prediction['loads']:load_bytes[row['src'],row['src_port']]+=row['framed_bytes']
    need(load_bytes==payload_bytes,'frozen per-link frames differ from actual UDP payload demand')
    for port in result['ports']:
        key=port['node'],port['port']
        need(data_bytes[key]==port['data_tx_bytes'] and control_bytes[key]==port['control_tx_bytes'],'port byte counter mismatch')
        need(data_bytes[key]+control_bytes[key]==port['model_tx_bytes'],'total port bytes mismatch')
    occupancy,last,area,peak=(Counter() for _ in range(4))
    pending={}
    fifo=defaultdict(deque)
    waits=Counter()
    maximum=0
    enqueues=0
    for row,key in tagged:
        time,node,port,event=row[:4]
        if event not in (3,4):continue
        need(kinds[node]!='gpu','unexpected GPU BEgressQueue event')
        resource=node,port
        area[resource]+=occupancy[resource]*(time-last[resource])
        last[resource]=time
        identity=(node,port,key)
        if event==3:
            need(identity not in pending,'duplicate enqueue')
            occupancy[resource]+=row[8]
            need(occupancy[resource]==row[10],'enqueue occupancy mismatch')
            pending[identity]=time
            fifo[resource,row[6]].append(identity)
            enqueues+=1
        else:
            need(identity in pending and occupancy[resource]==row[10],'dequeue mismatch')
            need(fifo[resource,row[6]].popleft()==identity,'within-protocol FIFO violation')
            occupancy[resource]-=row[8]
            wait=time-pending.pop(identity)
            need(wait>=0,'negative wait')
            own=[t for t in transmissions[resource] if t[2]==key]
            need(len(own)==1 and own[0][0]==time,'dequeue/physical TX mismatch')
            waits['total_ns']+=wait
            waits['data_ns' if row[6]==17 else 'control_ns']+=wait
            maximum=max(maximum,wait)
        peak[resource]=max(peak[resource],occupancy[resource])
    need(not pending and all(n==0 for n in occupancy.values()),'nonempty queue at drain')
    for port in result['ports']:
        if 'queue_metrics' not in port:continue
        key=port['node'],port['port']
        metric=port['queue_metrics']
        need(area[key]==metric['area_byte_ns'] and peak[key]==metric['peak_bytes'] and metric['final_bytes']==0,
             'queue integral, peak or drain mismatch')
    source_packets=result['transactions']['transport']['source_packets']
    need(sum(r[3]==0 and r[6]==17 and kinds[r[1]]=='gpu' for r,k in tagged)==len(source_packets),'source packet count mismatch')
    return dict(passed=True,framed_request_reply_bytes=sum(cells.values()),
        load_cells=len(cells),packet_lineages=len(packets),fixed_NIC_paths=paths_checked,
        physical_TX=sum(tx_count.values()),physical_RX=sum(rx_count.values()),
        switch_enqueues=enqueues,queue_wait_ns=dict(waits),maximum_packet_wait_ns=maximum,
        maximum_queue_bytes=max(peak.values(),default=0),
        cnp_transmissions=sum(r[3]==0 and r[6]==255 for r,k in tagged),
        UDP_payload_link_bytes=sum(payload_bytes.values()),all_model_link_bytes=sum(data_bytes.values())+sum(control_bytes.values()),
        per_link_load=[dict(node=n,port=p,framed_bytes=v) for (n,p),v in sorted(payload_bytes.items())],
        cnp_scope='actual IP/UID plus generated stream/fid projection; fid not serialized by capture',
        cumulative_wait_is_not_application_loss=True,FIFO_scope='within each captured IP protocol; no inferred cross-priority order')
