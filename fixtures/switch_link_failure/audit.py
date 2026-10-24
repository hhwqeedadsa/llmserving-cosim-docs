"""Independent, complete-capture audit of the switch-link functional examples.

Only stdlib and saved public JSON are used. This checker requires a drained
legacy-byte run, ACK-each-packet, no receive-error rules and all-switch snapshots.
It is not a performance model or a checker for partial captures/modern GIN.
"""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path


def need(condition, message):
    if not condition:
        raise ValueError(message)


def audit(topology, config, messages, result):
    capture = result['packet_capture']
    events = capture['events']
    need(capture['complete'] and capture['omitted_events'] == 0 and
         capture['observations'] == len(events), 'incomplete packet capture')
    selection = capture['selection']
    need(not selection['nodes'] and selection['start_ns'] == 0 and
         selection['end_ns'] >= result['finish_ns'], 'capture does not cover complete fabric/time')
    need(not config.get('faults') and not config.get('drop_once') and
         config['ack_interval_bytes'] == 1, 'unsupported receive-error/ACK configuration')
    need(result['passed'] and result['drained'], 'run not passed/drained')
    need(result['planes'] == 1, 'checker requires the single-plane functional examples')
    headers = capture['wire_headers']
    need(len(headers['event_header_indices']) == len(events), 'missing event wire header')
    peer, links = {}, {}
    for edge in topology['links']:
        a, b = (edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])
        peer[a], peer[b] = b, a
        links[a] = links[b] = edge
    switches = {n['id'] for n in topology['nodes'] if n['kind'] != 'gpu'}
    logical = {m['id']: m for m in messages}
    report = {m['id']: m for m in result['messages']}
    need(set(logical) == set(report), 'message set mismatch')
    failures = config['link_failures']['events']
    need(len({f['time_ns'] for f in failures}) == len(failures), 'checker requires distinct shutdown times')
    observed = result['link_failures']
    need(observed['configuration'] == config['link_failures'], 'fault configuration mismatch')
    need(observed['pending_events'] == 0 and observed['route_epoch'] == len(failures),
         'unfinished topology events')
    closed = {}
    for fault in failures:
        edge = next(e for e in topology['links'] if (e['src'], e['dst']) == (fault['src'], fault['dst']))
        closed[edge['src'], edge['src_port']] = fault['time_ns']
        closed[edge['dst'], edge['dst_port']] = fault['time_ns']

    distances = {}

    def check_route(node, port, mid, time):
        # Reconstruct reachability using only the faults whose declared control
        # delay elapsed. Physical IsLinkUp must not reveal other faults early.
        known = tuple(i for i, f in enumerate(failures)
                      if time >= f['time_ns']+f['route_update_delay_ns'])
        destination = logical[mid]['dst']
        cache_key = destination, known
        if cache_key not in distances:
            excluded = {(failures[i]['src'], failures[i]['dst']) for i in known}
            adjacency = defaultdict(list)
            for edge in topology['links']:
                if (edge['src'], edge['dst']) not in excluded:
                    adjacency[edge['src']].append(edge['dst'])
                    adjacency[edge['dst']].append(edge['src'])
            distance, todo = {destination: 0}, deque([destination])
            while todo:
                current = todo.popleft()
                for other in adjacency[current]:
                    if other not in distance:
                        distance[other] = distance[current]+1
                        if other in switches:
                            todo.append(other)
            distances[cache_key] = distance
        distance = distances[cache_key]
        need(node in distance and distance.get(peer[node, port][0]) == distance[node]-1,
             'forwarding violates shortest path with independently known failures')

    # Identity includes actual wire endpoints, lineage, protocol and sequence.
    # Each physical transmission gets a separate FIFO entry even if UID repeats.
    def identity(index):
        row = events[index]
        wire = headers['headers'][headers['event_header_indices'][index]]
        need(wire[0] == row[4] and wire[3] == row[6], 'wire-header identity mismatch')
        return row[4], row[6], row[7], row[8], tuple(wire[1:])

    flying, queues = defaultdict(deque), defaultdict(deque)
    arrivals, departures = {}, defaultdict(deque)
    incoming, outgoing = Counter(), Counter()
    ordinary, headroom = Counter(), Counter()
    reservations = Counter()
    for endpoint, edge in links.items():
        if endpoint[0] in switches:
            reservations[endpoint[0]] += 4096+(edge['rate_bps']//1000000*edge['delay_ns']//8000)*3
    next_expected, first_ready, final_ack = Counter(), {}, {}
    source_packets, source_payload = Counter(), Counter()
    unique_source, source_repeated = defaultdict(set), Counter()
    counts, tx_bytes, control_bytes, data_packets = Counter(), Counter(), Counter(), Counter()
    queue_bytes, queue_peak, queue_area, queue_time = Counter(), Counter(), Counter(), Counter()
    states = {}
    closure_drops, stale_drops = Counter(), 0
    data_port_history = defaultdict(list)

    def switch_state():
        state = {}
        for node in switches:
            port_in = {p: value for (n, p), value in incoming.items() if n == node}
            state[node] = dict(ingress=sum(port_in.values()),
                               egress=sum(value for (n, _), value in outgoing.items() if n == node),
                               shared=sum(max(0, value-4096) for (n, _), value in ordinary.items() if n == node),
                               queues={p: value for (n, p), value in queue_bytes.items() if n == node})
        return state

    last_time = -1
    for index, row in enumerate(events):
        t, node, port, kind, uid, mid, protocol, seq, size, ecn, native_q, payload = row
        need(t >= last_time and (node, port) in peer, 'invalid event order/physical port')
        if t != last_time:
            if last_time in {f['time_ns'] for f in failures}:
                states[last_time, 'after'] = switch_state()
            if t in {f['time_ns'] for f in failures}:
                states[t, 'before'] = switch_state()
            last_time = t
        ident, endpoint = identity(index), (node, port)
        counts[kind] += 1
        if kind == 0:
            need(endpoint not in closed or t < closed[endpoint], 'transmit on closed link')
            flying[peer[endpoint], ident].append((t, links[endpoint]))
            tx_bytes[endpoint] += size
            if protocol != 17:
                control_bytes[endpoint] += size
            else:
                data_packets[endpoint] += 1
                data_port_history[node, mid].append((t, port))
            if node in switches:
                need(departures[node, ident], 'switch TX without dequeue')
                need(departures[node, ident].popleft() == (t, port), 'dequeue/TX port mismatch')
            elif protocol == 17:
                need(mid in logical and logical[mid]['src'] == node, 'unknown source data')
                need(0 <= seq < seq+payload <= logical[mid]['bytes'], 'source data out of logical bounds')
                source_packets[mid] += 1
                source_payload[mid] += payload
                if (seq, payload) in unique_source[mid]:
                    source_repeated[mid] += 1
                unique_source[mid].add((seq, payload))
        elif kind in (1, 2):
            need(flying[endpoint, ident], 'arrival/drop without matching physical TX')
            sent, edge = flying[endpoint, ident].popleft()
            expected = sent + edge['delay_ns'] + size*8*10**9/edge['rate_bps']
            need(abs(t-expected) <= 1, 'serialization/propagation mismatch')
            if kind == 2:
                need(endpoint in closed and t >= closed[endpoint], 'physical drop on live receiver')
            else:
                need(endpoint not in closed or t < closed[endpoint], 'RX accepted on closed receiver')
                if node in switches and protocol == 17:
                    need((node, ident) not in arrivals, 'unconsumed switch ingress')
                    arrivals[node, ident] = port
                elif mid in logical and protocol == 17:
                    need(logical[mid]['dst'] == node, 'data at incorrect GPU')
                    if seq == next_expected[mid]:
                        next_expected[mid] += payload
                        if next_expected[mid] == logical[mid]['bytes']:
                            first_ready[mid] = t
                elif node not in switches and mid in logical and protocol in (252, 253):
                    need(logical[mid]['src'] == node and seq <= next_expected[mid], 'feedback precedes received bytes')
                    if protocol == 252 and seq == logical[mid]['bytes']:
                        final_ack.setdefault(mid, t)
        elif kind == 3:
            need(node in switches, 'unsupported NIC BEgressQueue event')
            # Native BEqEnqueue runs after per-priority bytes are incremented;
            # BEqDequeue runs before they are decremented.
            need(native_q == queue_bytes[endpoint]+size, 'enqueue queue snapshot mismatch')
            ingress = arrivals.pop((node, ident), None) if protocol == 17 else None
            if protocol == 17:
                need(ingress is not None, 'queued data without physical arrival')
                check_route(node, port, mid, t)
                shared = sum(max(0, value-4096) for (n, _), value in ordinary.items() if n == node)
                threshold = (config['switch_buffer_bytes']-reservations[node]-shared)//8
                if ordinary[node, ingress]+size-4096 > threshold:
                    headroom[node, ingress] += size
                else:
                    ordinary[node, ingress] += size
                incoming[node, ingress] += size
                outgoing[endpoint] += size
            queues[endpoint].append((ident, ingress))
            queue_area[endpoint] += queue_bytes[endpoint]*(t-queue_time[endpoint])
            queue_time[endpoint] = t
            queue_bytes[endpoint] += size
            queue_peak[endpoint] = max(queue_peak[endpoint], queue_bytes[endpoint])
        elif kind == 4:
            need(native_q == queue_bytes[endpoint], 'dequeue queue snapshot mismatch')
            entry = next((item for item in queues[endpoint] if item[0] == ident), None)
            need(entry is not None, 'dequeue without enqueue')
            queues[endpoint].remove(entry)
            if protocol == 17:
                from_headroom = min(headroom[node, entry[1]], size)
                headroom[node, entry[1]] -= from_headroom
                ordinary[node, entry[1]] -= size-from_headroom
                incoming[node, entry[1]] -= size
                outgoing[endpoint] -= size
                need(incoming[node, entry[1]] >= 0 and outgoing[endpoint] >= 0, 'negative MMU balance')
            queue_area[endpoint] += queue_bytes[endpoint]*(t-queue_time[endpoint])
            queue_time[endpoint] = t
            queue_bytes[endpoint] -= size
            departures[node, ident].append((t, port))
        elif kind == 5:
            # Shutdown drains an actual queue; stale forwarding drops immediately
            # at the ingress. Neither consumes another physical transmission.
            if departures[node, ident]:
                need(departures[node, ident].popleft() == (t, port) and closed.get(endpoint) == t,
                     'local queue drop outside link shutdown')
                closure_drops[t, node] += 1
            else:
                need(protocol == 17 and arrivals.pop((node, ident), None) == port,
                     'unaccounted local forwarding drop')
                stale_drops += 1
            need(native_q == queue_bytes[endpoint], 'local drop queue snapshot mismatch')
        else:
            raise ValueError('unknown packet event')
    if last_time in {f['time_ns'] for f in failures}:
        states[last_time, 'after'] = switch_state()
    need(not any(flying.values()), 'physical transmissions lack arrival/drop')
    need(not any(queues.values()) and not any(departures.values()) and not arrivals,
         'unconsumed local queue/forwarding records')
    need(not any(incoming.values()) and not any(outgoing.values()), 'MMU data remains after drain')
    need(counts[0] == counts[1]+counts[2], 'physical packet conservation failed')
    need(observed['local_drops'] == counts[5], 'local drop count mismatch')

    epoch, downs, updates = 0, set(), set()
    for event in observed['events']:
        fault = failures[event['event_index']]
        if event['kind'] == 'routes_updated':
            epoch += 1
            updates.add(event['event_index'])
            need(event['time_ns'] == fault['time_ns']+fault['route_update_delay_ns'] and
                 event['route_epoch'] == epoch and event['excluded_directed_ports'] == 2*len(updates),
                 'route update learns edge early/late or incorrect epoch')
        else:
            need(event['kind'] == 'link_down' and event['event_index'] not in downs and
                 event['time_ns'] == fault['time_ns'] and event['route_epoch'] == epoch, 'incorrect shutdown event')
            downs.add(event['event_index'])
            t = event['time_ns']
            nodes = [fault['src'], fault['dst']]
            need(event['local_drops_during_close'] == sum(closure_drops[t, n] for n in nodes), 'queued drop count mismatch')
            for side in ('before', 'after'):
                for summary in event['buffer_'+side]:
                    n = summary['node']
                    state = states[t, side][n]
                    port = event['src_port'] if n == event['src'] else event['dst_port']
                    need(summary['failed_port_queued_data_bytes'] == state['queues'].get(port, 0) and
                         summary['switch_ingress_and_headroom_bytes'] == state['ingress'] and
                         summary['switch_egress_mmu_bytes'] == state['egress'] and
                         summary['switch_shared_used_bytes'] == state['shared'], 'MMU closure summary mismatch')
            need(event['queued_bytes_before'] == sum(states[t, 'before'][n]['queues'].get(
                event['src_port'] if n == event['src'] else event['dst_port'], 0) for n in nodes), 'shutdown bytes mismatch')
    need(len(downs) == len(updates) == len(failures), 'missing/duplicate topology event')
    snapshot = result['mmu_snapshot']
    need(snapshot['complete_selected_switch_ports'] and set(snapshot['selected_nodes']) == switches,
         'incomplete final MMU snapshots')
    for switch in snapshot['switches']:
        need(switch['shared_used_bytes'] == 0, 'residual shared MMU bytes')
        for port in switch['ports']:
            need(port['link_up'] == ((switch['node'], port['port']) not in closed), 'incorrect final link state')
            for field in ('ingress_bytes', 'headroom_used_bytes', 'egress_mmu_bytes', 'queue_bytes', 'pfc_paused'):
                need(not any(port[field]), 'residual MMU/queue/PFC state')
    for port in result['ports']:
        key = port['node'], port['port']
        need(port['model_tx_bytes'] == tx_bytes[key] and port['control_tx_bytes'] == control_bytes[key] and
             port['data_packets'] == data_packets[key], 'port byte/packet summary mismatch')
        pfc_rx = sum(e[1:4] == [key[0], key[1], 1] and e[6] == 254 for e in events)
        need(port['pfc_pause']+port['pfc_resume'] == pfc_rx, 'PFC callbacks differ from actual control arrivals')
        if port['node'] in switches:
            metrics = port['queue_metrics']
            area = queue_area[key]+queue_bytes[key]*(result['finish_ns']-queue_time[key])
            need(metrics['area_byte_ns'] == area and metrics['peak_bytes'] == queue_peak[key] and
                 metrics['final_bytes'] == 0, 'queue integral/peak mismatch')
    for mid, message in logical.items():
        row = report[mid]
        need(next_expected[mid] == message['bytes'] == row['receiver_contiguous_bytes'], 'receiver incomplete')
        need(row['ack_ns'] == final_ack.get(mid) and row['receive_ready_ns'] == first_ready.get(mid) and
             row['sender_resource_released_ns'] == row['ack_ns'] and row['ack_callbacks'] == 1 and
             not row['sender_qp_present'], 'fabricated completion/resource release')
        need(row['source_data_packets'] == source_packets[mid] and
             row['source_data_payload_bytes'] == source_payload[mid] and
             sum(size for _, size in unique_source[mid]) == message['bytes'], 'source traffic mismatch')
    recovered_ports = {}
    for fault in failures:
        mid_ports = {}
        for mid in logical:
            history = data_port_history[fault['src'], mid]
            before = sorted({p for t, p in history if t < fault['time_ns']})
            after = sorted({p for t, p in history if t >= fault['time_ns']+fault['route_update_delay_ns']})
            mid_ports[str(mid)] = dict(before=before, after=after)
        recovered_ports[str((fault['src'], fault['dst']))] = mid_ports
    return dict(schema_version='switch-link-functional-audit/1', physical_tx=counts[0], physical_rx=counts[1],
                physical_drop=counts[2], queued_local_drop=sum(closure_drops.values()),
                stale_forwarding_local_drop=stale_drops, messages=len(messages),
                repeated_source_packets=dict(source_repeated), route_epochs=epoch,
                observed_source_switch_ports=recovered_ports,
                pfc_pause_callbacks=sum(p['pfc_pause'] for p in result['ports']),
                pfc_resume_callbacks=sum(p['pfc_resume'] for p in result['ports']),
                scope='complete single-plane legacy-byte examples, data priority 3; PFC callback totals, not decoded pause durations; no hardware calibration')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('topology', 'config', 'messages', 'result', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    output = audit(*(json.loads(getattr(args, name).read_text()) for name in ('topology', 'config', 'messages', 'result')))
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True)+'\n')
