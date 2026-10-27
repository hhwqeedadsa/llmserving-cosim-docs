"""Independent saved-packet acceptance; stdlib only, no platform routing imports."""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path
import struct


def load(path): return json.loads(path.read_bytes())
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
def need(value, why):
    if not value: raise ValueError(why)


def audit(topology, protocol, result):
    transaction = result.get('transactions')
    transport = transaction['transport'] if transaction else result['persistent_transport']
    streams = {s['id']: s for s in transport['streams']}
    by_port = {s['wire_port']: s for s in streams.values()}
    bindings = {(r['gpu'], r['nic']): r for r in result['physical_nic_addressing']['bindings']}
    nodes = {r['id']: r for r in topology['nodes']}
    peers, links = {}, {}
    for edge in topology['links']:
        a, b = (edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])
        peers[a], peers[b] = b, a
        links[a] = links[b] = edge
    for s in streams.values():
        for side, nic in (('src', 'source'), ('dst', 'destination')):
            gpu, port = s[side], s[nic+'_nic']
            need((gpu, port) in peers and nodes[peers[gpu, port][0]]['plane'] == s['plane'], 'invalid NIC attachment')
            b = bindings[gpu, port]
            need(b['ipv4'] == 0x0c000000+gpu*2048+port and b['physical_alias'] and b['plane'] == s['plane'], 'wrong physical address')
            need(s[nic+'_ipv4'] == b['ipv4'], 'stream address differs from physical endpoint')
    capture = result['packet_capture']
    need(capture['complete'] and capture['omitted_events'] == 0 and
         capture['observations'] == len(capture['events']), 'incomplete capture')
    need(not capture['selection']['nodes'] and capture['selection']['start_ns'] == 0 and
         capture['selection']['end_ns'] > result['finish_ns'], 'capture scope excludes events')
    headers = capture['wire_headers']
    need(len(headers['event_header_indices']) == len(capture['events']), 'missing wire headers')
    cnp_streams = {e['packet_uid']: streams[e['stream']]
                   for e in transport.get('dcqcn_reference', {}).get('events', []) if e['kind'] == 'cnp_generated'}
    addresses = {b['ipv4']: (b['gpu'], b['nic']) for b in bindings.values()}
    failures = protocol.get('link_failures', {}).get('events', [])
    closed = {}
    for f in failures:
        edge = next(e for e in topology['links'] if (e['src'], e['dst']) == (f['src'], f['dst']))
        closed[edge['src'], edge['src_port']] = f['time_ns']
        closed[edge['dst'], edge['dst_port']] = f['time_ns']
    route_cache = {}

    def check_switch_route(node, port, destination, time):
        known = tuple((f['src'], f['dst']) for f in failures if time >= f['time_ns']+f['route_update_delay_ns'])
        key = destination, known
        if key not in route_cache:
            target = addresses[destination]
            adj = defaultdict(list)
            for edge in topology['links']:
                if (edge['src'], edge['dst']) in known: continue
                a, b = (edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])
                if ((nodes[a[0]]['kind'] == 'gpu' and a != target) or
                    (nodes[b[0]]['kind'] == 'gpu' and b != target)): continue
                adj[a[0]].append(b[0]); adj[b[0]].append(a[0])
            distance, todo = {target[0]: 0}, deque([target[0]])
            while todo:
                here = todo.popleft()
                for other in adj[here]:
                    if other not in distance:
                        distance[other] = distance[here]+1; todo.append(other)
            route_cache[key] = distance
        distance = route_cache[key]
        need(node in distance and distance.get(peers[node, port][0]) == distance[node]-1,
             'switch forwarding violates destination-NIC shortest route')

    flying, switch_resident = defaultdict(deque), Counter()
    tx_bytes, counts, source_bytes, source_count = Counter(), Counter(), Counter(), Counter()
    queue_bytes, queue_peak, queue_area, queue_time, queue_changes = (Counter() for _ in range(5))
    queue_fifo, mixed_queues = defaultdict(deque), set()
    received, acked = Counter(), Counter()
    receive_times, ack_times, source_rows = defaultdict(list), defaultdict(list), []
    serial_end = Counter()
    for index, event in enumerate(capture['events']):
        time, node, port, kind, uid, _, proto, seq, size, _, qbytes, payload = event
        endpoint = node, port
        wire = headers['headers'][headers['event_header_indices'][index]]
        need(wire[0] == uid and wire[3] == proto and endpoint in peers, 'wire/endpoint identity mismatch')
        need(proto in (17, 252, 253, 255), 'unexpected control protocol in acceptance')
        stream = cnp_streams.get(uid) if proto == 255 else by_port.get(wire[4])
        need(stream is not None, 'unassociated packet')
        sid = stream['id']
        forward = proto == 17
        a = (stream['src'], stream['source_nic']) if forward else (stream['dst'], stream['destination_nic'])
        b = (stream['dst'], stream['destination_nic']) if forward else (stream['src'], stream['source_nic'])
        expected_ip = (addresses[wire[1]], addresses[wire[2]])
        need(expected_ip == (a, b), 'packet wire addresses differ from bound NICs')
        if proto != 255: need(wire[4] == wire[5] == stream['wire_port'] and wire[6] == 1, 'wrong connection port')
        key = uid, proto, seq, size, tuple(wire[1:])
        counts[kind] += 1
        if kind == 0:
            need(endpoint not in closed or time < closed[endpoint], 'transmission on failed link')
            need(time+1 >= serial_end[endpoint], 'overlapping physical serialization')
            serial_end[endpoint] = time+size*8*10**9/links[endpoint]['rate_bps']
            tx_bytes[endpoint] += size
            flying[peers[endpoint], key].append(time)
            if nodes[node]['kind'] == 'gpu':
                need(endpoint == a, 'source/feedback transmitted on wrong physical NIC')
                if forward:
                    source_count[sid] += 1; source_bytes[sid] += payload
                    source_rows.append([time, sid, node, stream['dst'], port, seq, payload, uid])
            else:
                need(switch_resident[node, key] > 0, 'switch TX without a preceding arrival')
                switch_resident[node, key] -= 1
                check_switch_route(node, port, wire[2], time)
        elif kind in (1, 2):
            need(flying[endpoint, key], 'arrival without link transmission')
            sent = flying[endpoint, key].popleft()
            expected = links[endpoint]['delay_ns']+size*8*10**9/links[endpoint]['rate_bps']
            need(abs(time-sent-expected) <= 1, 'incorrect link serialization/propagation')
            if kind == 1:
                if nodes[node]['kind'] != 'gpu': switch_resident[node, key] += 1
                else:
                    need(endpoint == b, 'packet arrived on wrong physical NIC')
                    if forward and seq == received[sid]:
                        received[sid] += payload; receive_times[sid].append((received[sid], time))
                    elif proto in (252, 253):
                        need(seq <= received[sid], 'ACK covers unavailable bytes')
                        if seq > acked[sid]: acked[sid] = seq; ack_times[sid].append((seq, time))
        elif kind in (3, 4):
            if nodes[node]['kind'] == 'gpu': continue
            queue_area[endpoint] += queue_bytes[endpoint]*(time-queue_time[endpoint]); queue_time[endpoint] = time
            if kind == 3:
                queue_bytes[endpoint] += size; queue_fifo[endpoint, proto == 17].append((key, sid))
                occupants = {streams[s]['source_ipv4'] for _, s in queue_fifo[endpoint, True]}
                if len(occupants) > 1: mixed_queues.add(endpoint)
                need(qbytes == queue_bytes[endpoint], 'enqueue occupancy mismatch')
            else:
                need(qbytes == queue_bytes[endpoint], 'dequeue occupancy mismatch')
                need(queue_fifo[endpoint, proto == 17] and queue_fifo[endpoint, proto == 17].popleft() == (key, sid), 'priority FIFO mismatch')
                queue_bytes[endpoint] -= size
            need(queue_bytes[endpoint] >= 0, 'negative queue')
            queue_peak[endpoint] = max(queue_peak[endpoint], queue_bytes[endpoint]); queue_changes[endpoint] += 1
        elif kind == 5:
            need(switch_resident[node, key] > 0, 'local drop without switch arrival')
            switch_resident[node, key] -= 1
        else: raise ValueError('unknown packet event')
    need(not any(flying.values()) and not any(switch_resident.values()), 'unaccounted live packet')
    need(counts[0] == counts[1]+counts[2], 'physical packet conservation failed')
    need(source_rows == transport['source_packets'], 'source packet report mismatch')
    for port in result['ports']:
        endpoint = port['node'], port['port']
        need(port['model_tx_bytes'] == tx_bytes[endpoint], 'port byte counter mismatch')
        if 'queue_metrics' in port:
            area = queue_area[endpoint]+queue_bytes[endpoint]*(result['finish_ns']-queue_time[endpoint])
            need(port['queue_metrics'] == dict(area_byte_ns=area, peak_bytes=queue_peak[endpoint],
                 final_bytes=queue_bytes[endpoint], changes=queue_changes[endpoint]), 'independent queue integral mismatch')
    for s in streams.values():
        sid = s['id']
        need(s['qp_creations'] == 1 and s['snd_una'] == acked[sid] and s['source_data_packets'] == source_count[sid]
             and s['source_payload_bytes'] == source_bytes[sid], 'QP/prefix/source totals mismatch')
        if result['passed']: need(received[sid] == acked[sid] == s['published_bytes'], 'incomplete successful stream')
    for work in transport['work']:
        sid, end = work['stream'], work['end']
        need(work['accepted_ns'] == next((t for prefix, t in receive_times[sid] if prefix >= end), -1), 'fabricated acceptance time')
        need(work['acked_ns'] == next((t for prefix, t in ack_times[sid] if prefix >= end), -1), 'fabricated ACK time')
    if failures:
        state = result['link_failures']
        need(state['configuration'] == protocol['link_failures'] and state['pending_events'] == 0 and
             state['route_epoch'] == len(failures), 'route rebuilding incomplete')
        need([e['time_ns'] for e in state['events'] if e['kind'] == 'routes_updated'] ==
             [f['time_ns']+f['route_update_delay_ns'] for f in failures], 'incorrect update timing')
    return dict(passed=True, physical_tx=counts[0], physical_rx=counts[1], arrival_drops=counts[2],
                local_drops=counts[5], source_packets=sum(source_count.values()), streams=len(streams),
                work=len(transport['work']), shared_queue_ports=[list(p) for p in sorted(mixed_queues)],
                cnp_packets=len(cnp_streams), finish_ns=result['finish_ns'], network_passed=result['passed'])


def transactions(program, result):
    actual = result['transactions']; transport = actual['transport']
    # Independently assemble request/response byte streams from source operands.
    payload = defaultdict(bytearray)
    for packet in transport['source_payload_packets']:
        need(packet['sequence'] == len(payload[packet['stream']]), 'split/reordered source frame')
        payload[packet['stream']].extend(bytes.fromhex(packet['hex']))
    frames = {}
    for sid, raw in payload.items():
        offset, ordinal, decoded = 0, 0, []
        while offset < len(raw):
            magic, verb, seq, address, key, length = struct.unpack('!IIQQII', raw[offset:offset+32])
            need(magic == 0x4d525431 and (sid % 2 or seq == ordinal), 'invalid MRT1 header')
            body = bytes(raw[offset+32:offset+32+length]); need(len(body) == length, 'truncated MRT1 frame')
            decoded.append((verb, seq, address, key, body)); offset += 32+length; ordinal += 1
        frames[sid] = decoded
    conns = {c['id']: c for c in program['connections']}
    windows = {(w['owner'], w['key']): (w['address'], bytearray.fromhex(w['hex'])) for w in program['windows']}
    work = {sid: sorted((w for w in transport['work'] if w['stream'] == sid), key=lambda w:w['begin']) for sid in frames}
    ordinals, returned = Counter(), {}
    targets = sorted(actual['target_events'], key=lambda e:e['order'])
    # Packet auditor has already verified actual acceptance/ACK prefixes.
    for order, target in enumerate(targets):
        cid, ordinal = target['connection'], target['sequence']
        operation = [o for o in program['operations'] if o['connection'] == cid][ordinal]
        verb, seq, address, key, body = frames[cid*2][ordinal]
        need((verb, seq, address, key, body.hex()) ==
             (target['verb'], ordinal, target['address'], target['key'], target['body_hex']), 'target differs from source MRT1')
        need(target['order'] == order and target['time_ns'] == work[cid*2][ordinal]['accepted_ns'],
             'target side effect precedes actual request acceptance')
        expected = bytes.fromhex(operation['hex']) if operation['verb'] == 'put' else operation['value'].to_bytes(8, 'big')
        need((address, key, body) == (operation['address'], operation['key'], expected), 'wire request differs from input')
        base, memory = windows[conns[cid]['dst'], key]; offset = address-base
        need(0 <= offset and offset+len(body) <= len(memory), 'target outside declared memory')
        old = 0
        if verb == 1: memory[offset:offset+len(body)] = body
        else:
            need(verb == 2, 'unexpected request verb'); old = int.from_bytes(memory[offset:offset+8], 'little')
            memory[offset:offset+8] = ((old+int.from_bytes(body, 'big')) % 2**64).to_bytes(8, 'little')
            returned[cid, ordinal] = old
        need(target['old_value'] == old and not target['duplicate'], 'duplicate/incorrect target side effect')
        ordinals[cid] += 1
    need(len(targets) == len(program['operations']), 'missing target operation')
    for w in actual['windows']: need(w['hex'] == windows[w['owner'], w['key']][1].hex(), 'wrong final memory')
    response_times = {}
    for cid in conns:
        replies = frames.get(cid*2+1, [])
        expected = [(seq, value) for (c, seq), value in returned.items() if c == cid]
        need([(frame[1], int.from_bytes(frame[4], 'big')) for frame in replies] == expected, 'wrong actual atomic reply')
        for index, frame in enumerate(replies): response_times[cid, frame[1]] = work[cid*2+1][index]['accepted_ns']
    for op in actual['operations']:
        cid, ordinal = op['connection'], op['sequence']
        request = work[cid*2][ordinal]
        need(op['applied_ns'] == request['accepted_ns'] and op['request_acked_ns'] == request['acked_ns'], 'operation times differ from physical request callbacks')
        if op['verb'] == 'fetch_add':
            need(op['returned_value'] == returned[cid, ordinal] and op['response_ns'] == response_times[cid, ordinal], 'wrong returned value/arrival')
        need(op['completed_ns'] == max(op['request_acked_ns'], op['response_ns']), 'premature operation completion')
    return dict(operations=len(targets), atomic_replies=len(returned), final_atomic_value=int.from_bytes(windows[4, 7][1][:8], 'little'))


def check(stage):
    conditions = [('independent','independent','protocol.json'), ('shared-congestion','shared-slow','congested-protocol.json'),
                  ('transactions','shared','protocol.json'), ('modern','shared','protocol.json'),
                  ('alternate','redundant','alternate-protocol.json'), ('partition','shared','partition-protocol.json')]
    summary = {}
    for name, topo, protocol in conditions:
        summary[name] = audit(load(stage/(topo+'-topology/topology.json')), load(stage/'inputs'/protocol), load(stage/name/'result.json'))
    need(summary['shared-congestion']['cnp_packets'] > 0 and summary['shared-congestion']['shared_queue_ports'], 'no actual shared contention/feedback')
    summary['transactions']['memory'] = transactions(load(stage/'inputs/transaction-work.json'), load(stage/'transactions/result.json'))
    p = load(stage/'partition/result.json'); streams = p['persistent_transport']['streams']
    need(not p['passed'] and p['completed_work'] == 3 and p['finish_ns'] == 560000, 'partition not finite failed work')
    need(streams[0]['snd_una'] == 0 and streams[2]['snd_una'] == 32768 and streams[0]['src'] == streams[2]['src'] == 0,
         'failed source migrated or healthy second NIC failed')
    need(streams[0]['source_nic'] == 1 and streams[0]['source_data_packets'] == 99, 'missing same-NIC retries')
    modern = load(stage/'modern/modern-execution.json')
    need(modern['runtime']['all_events_fired'] and modern['runtime']['all_application_complete'], 'modern graph incomplete')
    summary['modern']['application_events'] = modern['runtime']['total_events']
    summary['single_compatibility'] = load(stage/'single-compatibility.json')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    save(args.output, check(args.stage))


if __name__ == '__main__': main()
