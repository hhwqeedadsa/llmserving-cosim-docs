"""Post-hoc physical resource and queue-wait accounting, never an online policy."""
import argparse
from collections import Counter, defaultdict, deque
from itertools import combinations
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def ip(gpu,plane):
    return 0x0b000001+32*gpu+plane


def components(vertices, resources):
    parent = {v:v for v in vertices}
    def root(v):
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v
    edges = {}
    for resource, owners in sorted(resources.items()):
        for a,b in combinations(sorted(owners),2):
            parent[root(a)] = root(b)
            edges.setdefault((a,b),resource)
    groups = defaultdict(list)
    for v in sorted(vertices):
        groups[root(v)].append(v)
    return dict(component_sizes=sorted(map(len,groups.values()),reverse=True),
        components=sorted(groups.values()),direct_connection_pairs=len(edges),
        shared_resource_count=sum(len(v)>1 for v in resources.values()),
        pair_witnesses=[dict(connections=list(pair),resource=list(resource)) for pair,resource in sorted(edges.items())])


def structural_rails(topology,initial,active):
    nodes = {n['id']:n for n in topology['nodes']}
    ports = {}
    for link in topology['links']:
        a,b = nodes[link['src']],nodes[link['dst']]
        if a['kind']!='gpu' and b['kind']!='gpu':
            require(a['plane']==b['plane'],'cross-plane switch link; rail certificate unavailable')
        for src,dst,port in ((a,b,link['src_port']),(b,a,link['dst_port'])):
            if src['kind']=='gpu':
                require(dst['kind']!='gpu','GPU-to-GPU link outside declared rail model')
                ports[src['id'],port] = dst['plane']
    grouped = defaultdict(list)
    for c in initial['connections']:
        if c['id'] not in active:
            continue
        require(ports[c['src'],c['source_nic']]==c['plane']==ports[c['dst'],c['destination_nic']],
                'connection/NIC plane disagreement')
        grouped[c['plane']].append(c['id'])
    return dict(switch_links_do_not_cross_planes=True,connection_NICs_match_planes=True,
                active_connections_per_plane={str(k):sorted(v) for k,v in sorted(grouped.items())},
                independent_application_execution_proven=False,
                scope='structural switch/link/NIC partition; shared GPU lanes, callbacks, RNG and application gates still need treatment')


def analyze(directory):
    topology, protocol, initial, result = [load(directory/name) for name in ('topology.json','protocol.json','initial.json','result.json')]
    cap = result['packet_capture']
    require(result['passed'] and result['drained'] and cap['complete'] and cap['omitted_events']==0,'incomplete execution/capture')
    require(not protocol['packet_capture']['nodes'] and protocol['packet_capture']['start_ns']==0 and
            protocol['packet_capture']['end_ns']>=result['finish_ns'],'capture must cover all nodes and complete time')
    require(all(p['pfc_pause']==p['pfc_resume']==p['ecn_ce_seen']==0 for p in result['ports']),
            'this first audit only accepts the saved no-feedback records')
    peers = {}
    for link in topology['links']:
        for a,b,ap,bp in ((link['src'],link['dst'],link['src_port'],link['dst_port']),
                          (link['dst'],link['src'],link['dst_port'],link['src_port'])):
            peers[a,ap] = (b,bp,link['rate_bps'],link['delay_ns'])
    nodes = {n['id']:n['kind'] for n in topology['nodes']}
    wires = {}
    for connection in initial['connections']:
        if 'self_service' in connection:
            continue
        for kind,src,dst,port in (('request',connection['src'],connection['dst'],connection['request_port']),
                                  ('response',connection['dst'],connection['src'],connection['response_port'])):
            key = (ip(src,connection['plane']),ip(dst,connection['plane']),port,port)
            require(key not in wires,'ambiguous wire identity')
            wires[key] = (connection['id'],kind,src,dst)
    headers = cap['wire_headers']['headers']
    indices = cap['wire_headers']['event_header_indices']
    require(len(indices)==len(cap['events']),'wire header index count mismatch')
    packets = defaultdict(list)
    tagged = []
    for ordinal,(row,header_index) in enumerate(zip(cap['events'],indices)):
        header = headers[header_index]
        require(header[0]==row[4] and header[3]==row[6] and header[6]==1,'header/capture mismatch')
        key = (header[1],header[2],header[4],header[5])
        if row[6]==17:
            owner = wires.get(key)
            kind = 'udp'
        elif row[6]==252:
            owner = wires.get((key[1],key[0],key[3],key[2]))
            kind = 'ack'
        else:
            raise ValueError('unsupported loss/feedback protocol: '+str(row[6]))
        require(owner is not None,'unmapped packet wire tuple')
        connection,direction,src,dst = owner
        if kind=='ack':
            src,dst = dst,src
        identity = tuple(header)+(row[7],row[8],row[11])
        item = dict(ordinal=ordinal,row=row,key=identity,connection=connection,direction=direction,
                    protocol=kind,src=src,dst=dst)
        packets[identity].append(item)
        tagged.append(item)

    paths = []
    transmissions = defaultdict(list)
    active = set()
    request_resources,all_resources,mmu_resources = (defaultdict(set) for _ in range(3))
    for key, records in packets.items():
        tags = {(r['connection'],r['direction'],r['protocol'],r['src'],r['dst']) for r in records}
        require(len(tags)==1,'packet tag changed within captured lineage')
        connection,direction,kind,src,dst = tags.pop()
        active.add(connection)
        events = defaultdict(list)
        for r in records:
            row = r['row']
            require(row[3] in (0,1,3,4),'drop/unknown event in complete normal record')
            events[row[1],row[3]].append(r)
        current = src
        used_tx = used_rx = 0
        hops = []
        visited = set()
        previous_rx = None
        while current != dst:
            require(current not in visited,'cyclic observed route')
            visited.add(current)
            txs = events[current,0]
            require(len(txs)==1,'ambiguous retransmission/route lineage')
            tx = txs[0]['row']
            peer,peer_port,rate,delay = peers[current,tx[2]]
            receives = [r['row'] for r in events[peer,1] if r['row'][2]==peer_port]
            require(len(receives)==1,'missing/ambiguous physical receive')
            rx = receives[0]
            duration = (tx[8]*8*10**9+rate//2)//rate
            require(rx[0]-tx[0]==duration+delay,'link serialization/propagation mismatch')
            if previous_rx is not None:
                require(tx[0]>=previous_rx,'forward before arrival')
            resource = ('port',current,tx[2])
            all_resources[resource].add(connection)
            if kind=='udp' and direction=='request':
                request_resources[resource].add(connection)
            if nodes[current]!='gpu' and kind=='udp':
                mmu_resources['mmu',current].add(connection)
            transmissions[current,tx[2]].append(dict(start=tx[0],end=tx[0]+duration,
                connection=connection,protocol=kind,packet_uid=tx[4],key=key,bytes=tx[8]))
            hops.append(dict(node=current,port=tx[2],peer=peer,peer_port=peer_port,
                             tx_ns=tx[0],rx_ns=rx[0],service_ns=duration,model_bytes=tx[8]))
            used_tx += 1
            used_rx += 1
            previous_rx = rx[0]
            current = peer
        require(sum(len(v) for (n,e),v in events.items() if e==0)==used_tx and
                sum(len(v) for (n,e),v in events.items() if e==1)==used_rx,'unaccounted physical hop')
        paths.append(dict(packet_uid=key[0],connection=connection,direction=direction,protocol=kind,
                          source=src,destination=dst,hops=hops))

    # Each serializer must have nonoverlapping occupied intervals.
    for intervals in transmissions.values():
        intervals.sort(key=lambda x:x['start'])
        require(all(a['end']<=b['start'] for a,b in zip(intervals,intervals[1:])), 'physical transmitter overlap')
    for port in result['ports']:
        require(sum(tx['bytes'] for tx in transmissions[port['node'],port['port']])==port['model_tx_bytes'],
                'complete path service bytes disagree with native port counters')
    # Reconstruct every switch queue state and packet's enqueue-to-dequeue wait.
    occupancy,last,area,peak = (defaultdict(int) for _ in range(4))
    pending = {}
    fifo = defaultdict(deque)
    waits = []
    wait_totals = Counter()
    for item in tagged:
        row = item['row']
        time,node,port,event = row[:4]
        if event not in (3,4):
            continue
        require(nodes[node]!='gpu','unexpected NIC BEgressQueue hook')
        resource = node,port
        area[resource] += occupancy[resource]*(time-last[resource])
        last[resource] = time
        key = (node,port,item['key'])
        if event==3:
            require(key not in pending,'duplicate enqueue lineage')
            occupancy[resource] += row[8]
            require(occupancy[resource]==row[10],'enqueue occupancy mismatch')
            pending[key] = item
            fifo[resource,item['protocol']].append(key)
        else:
            require(occupancy[resource]==row[10] and key in pending,'dequeue occupancy/lineage mismatch')
            require(fifo[resource,item['protocol']].popleft()==key,'priority-queue FIFO order changed')
            occupancy[resource] -= row[8]
            entered = pending.pop(key)
            arrival = entered['row'][0]
            wait = time-arrival
            own = [tx for tx in transmissions[resource] if tx['key']==item['key']]
            require(len(own)==1 and own[0]['start']==time,'dequeue did not immediately start this packet')
            blocks = []
            assigned = Counter()
            for prior in transmissions[resource]:
                overlap = max(0,min(time,prior['end'])-max(arrival,prior['start']))
                if overlap:
                    label = 'same_connection' if prior['connection']==item['connection'] else 'other_connection'
                    assigned[label] += overlap
                    if prior['protocol']=='ack':
                        assigned['ack_serialization'] += overlap
                    blocks.append(dict(connection=prior['connection'],protocol=prior['protocol'],
                                       packet_uid=prior['packet_uid'],overlap_ns=overlap))
            unexplained = wait-assigned['same_connection']-assigned['other_connection']
            require(unexplained>=0,'overlapping wait attribution')
            wait_totals.update(dict(wait_ns=wait,same_connection_ns=assigned['same_connection'],
                other_connection_ns=assigned['other_connection'],ack_serialization_ns=assigned['ack_serialization'],
                unexplained_ns=unexplained))
            waits.append(dict(node=node,port=port,packet_uid=row[4],connection=item['connection'],
                protocol=item['protocol'],enqueue_ns=arrival,dequeue_ns=time,wait_ns=wait,
                blockers=blocks,unexplained_ns=unexplained))
        peak[resource] = max(peak[resource],occupancy[resource])
    require(not pending and all(v==0 for v in occupancy.values()),'queue did not drain')
    for port in result['ports']:
        if 'queue_metrics' not in port:
            continue
        key = port['node'],port['port']
        expected = port['queue_metrics']
        require(area[key]==expected['area_byte_ns'] and peak[key]==expected['peak_bytes'] and expected['final_bytes']==0,
                'independent queue integral/peak mismatch')
    source_packets = result['transactions']['transport']['source_packets']
    require(len({r[7] for r in source_packets})==len(source_packets),'source UID reused; extend attribution before continuing')
    require(sum(p['protocol']=='udp' for p in paths)==len(source_packets),'not all source packets reconstructed')
    combined = defaultdict(set,{k:set(v) for k,v in all_resources.items()})
    for key,owners in mmu_resources.items():
        combined[key].update(owners)
    physical_tx_count = sum(len(v) for v in transmissions.values())
    require(sum(row[3]==0 for row in cap['events'])==physical_tx_count,'physical TX count mismatch')
    plan = load(directory/'modern-plan.json')
    logical_resources = defaultdict(set)
    for event in plan['events']:
        for parent in event['deps']:
            logical_resources['event',parent].update((event['id'],parent))
    application_components = components({e['id'] for e in plan['events']},logical_resources)
    summary = dict(scope='posthoc_physical_interactions_not_online_safe_partition',
        active_connections=len(active), configured_connections=len(initial['connections']),
        configured_self_connections=sum('self_service' in c for c in initial['connections']),
        source_udp_packets=len(source_packets),source_ack_packets=sum(p['protocol']=='ack' for p in paths),
        complete_packet_paths=len(paths),physical_transmissions=physical_tx_count,
        captured_events=len(cap['events']),
        request_only_port_sharing=components(active,request_resources),
        full_direction_port_sharing=components(active,all_resources),
        full_ports_and_shared_MMU=components(active,combined),
        structural_rail_certificate=structural_rails(topology,initial,active),
        application_event_graph=dict(events=len(plan['events']),
            weak_component_sizes=application_components['component_sizes'],
            scope='logical dependency connectivity is not a mandatory packet-refinement domain'),
        per_packet_queue_wait=dict(wait_totals), queued_packets=len(waits),
        positive_wait_packets=sum(w['wait_ns']>0 for w in waits),
        max_packet_wait_ns=max(w['wait_ns'] for w in waits),
        longest_waits=sorted(waits,key=lambda w:w['wait_ns'],reverse=True)[:8],
        queue_integrals_independently_verified=True,
        source_nic_queue_wait_not_observed=True,queue_wait_sum_is_not_application_speedup=True,
        packet_simulation_cost_reduction_measured=False,hardware_calibrated=False)
    return summary,dict(paths=paths,positive_waits=[w for w in waits if w['wait_ns']>0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    summaries = {}
    for name in ('baseline','bandwidth'):
        summaries[name],witnesses = analyze(args.stage/name)
        save(args.output_dir/(name+'-witnesses.json'),witnesses)
    save(args.output_dir/'summary.json',summaries)
    print(json.dumps({k:{field:v[field] for field in ('active_connections','source_udp_packets','complete_packet_paths',
         'physical_transmissions','positive_wait_packets','per_packet_queue_wait')} for k,v in summaries.items()}))


if __name__ == '__main__':
    main()
