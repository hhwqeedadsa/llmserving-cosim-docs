"""Independent packet/port/ACK and observed-order graph checker; no analyzer import."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def verify(result, topology, plan, graph):
    capture = result['packet_capture']
    assert result['passed'] and result['drained'] and capture['complete']
    ev = capture['events']
    htab = capture['wire_headers']
    assert len(htab['event_header_indices']) == len(ev)
    wire = [tuple(htab['headers'][i]) for i in htab['event_header_indices']]
    assert all((h[0], h[3], h[6]) == (e[4], e[6], 1) for h, e in zip(wire, ev))
    endpoint = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    peer, serial_rate, delay = {}, {}, {}
    for link in topology['links']:
        a, b = (link['src'], link['src_port']), (link['dst'], link['dst_port'])
        peer[a], peer[b] = b, a
        for port in (a, b):
            serial_rate[port], delay[port] = link['rate_bps'], link['delay_ns']
    transmissions = [i for i in range(len(ev)) if ev[i][3] == 0]
    tx_id = {event: j for j, event in enumerate(transmissions)}
    assert [r[0] for r in graph['nodes']] == transmissions
    by_port_packet = {}
    by_rx = {}
    packet_tx_count = Counter()
    for i in range(len(ev)):
        event, head = ev[i], wire[i]
        assert event[6] in (17, 252) and event[3] in (0, 1, 3, 4)
        key = event[1], event[2], head
        if event[3] == 0:
            assert key not in by_port_packet
            by_port_packet[key] = i
            packet_tx_count[head] += 1
        if event[3] == 1:
            assert key not in by_rx
            by_rx[key] = i
    serialization = {}
    # Independent integer divmod rounding, matched to fixed positive ns-3 rates.
    for i in transmissions:
        event = ev[i]
        q, rem = divmod(event[8]*8000000000, serial_rate[event[1], event[2]])
        serialization[i] = q + (2*rem >= serial_rate[event[1], event[2]])
        remote = peer[event[1], event[2]]
        receive = ev[by_rx[(*remote, wire[i])]]
        assert receive[0] == event[0]+serialization[i]+delay[event[1], event[2]]
        assert receive[6:10] == event[6:10] and receive[11] == event[11]
    streams = {s['id']: s for s in plan['streams']}
    source_by_uid = {}
    for row in result['persistent_transport']['source_packets']:
        stamp, sid, src, dst, nic, seq, payload, uid = row
        s = streams[sid]
        assert (src, dst, nic) == (s['src'], s['dst'], s['source_nic'])
        head = (uid, 0x0b000001+src*32+s['plane'], 0x0b000001+dst*32+s['plane'],
                17, s['wire_port'], s['wire_port'], 1)
        source = by_port_packet[src, nic, head]
        assert ev[source][0] == stamp and ev[source][7] == seq and ev[source][11] == payload
        assert uid not in source_by_uid
        source_by_uid[uid] = source
    assert all(w['ready_ns'] == w['published_ns'] == 0 for w in result['persistent_transport']['work'])
    data_triggers, reverse_ack = {}, {}
    packet_terminal = {}
    hops = defaultdict(list)
    for i in transmissions:
        event, head = ev[i], wire[i]
        hops[head].append(i)
        remote = peer[event[1], event[2]]
        if remote[0] in endpoint:
            rx = by_rx[(*remote, head)]
            packet_terminal[head] = (tx_id[i], serialization[i], delay[event[1], event[2]], ev[rx][0], rx)
            if event[6] == 17:
                key = head[2], head[1], head[5], head[4], event[7]+event[11]
                assert key not in data_triggers
                data_triggers[key] = i, rx
        if event[1] in endpoint and event[6] == 252:
            key = head[1], head[2], head[4], head[5], event[7]
            assert key not in reverse_ack
            reverse_ack[key] = i
    assert data_triggers.keys() == reverse_ack.keys()
    ack_links = {}
    for key, (data_tx, data_rx) in data_triggers.items():
        ack_tx = reverse_ack[key]
        assert ev[ack_tx][0] >= ev[data_rx][0]
        ack_links[ack_tx] = data_tx, data_rx
    assert graph['ack_matches'] == sorted([a, rx] for a, (_, rx) in ack_links.items())
    # Require each packet to follow one contiguous physical path, no retransmit.
    for head, path in hops.items():
        assert ev[path[0]][1] in endpoint and len(path) == packet_tx_count[head]
        assert head in packet_terminal
        assert all(peer[ev[a][1], ev[a][2]][0] == ev[b][1] for a, b in zip(path, path[1:]))
        if head[3] == 17:
            assert source_by_uid[head[0]] == path[0]
    preceding_hop = {b: a for path in hops.values() for a, b in zip(path, path[1:])}
    previous = {}
    earliest_values = []
    expected_ties = []
    propagation_min, propagation_max = [], []
    port_service = Counter()
    all_gaps = Counter()
    for j, i in enumerate(transmissions):
        event = ev[i]
        port = event[1], event[2]
        edges = []
        if event[1] not in endpoint:
            parent = preceding_hop[i]
            edges.append([tx_id[parent], serialization[parent], delay[ev[parent][1], ev[parent][2]], 'arrival'])
        elif event[6] == 17:
            edges.append([-1, 0, 0, 'published'])
        else:
            parent, _ = ack_links[i]
            edges.append([tx_id[parent], serialization[parent], delay[ev[parent][1], ev[parent][2]], 'ack_trigger'])
        if port in previous:
            parent = previous[port]
            edges.append([tx_id[parent], serialization[parent], 0, 'port'])
        previous[port] = i
        saved = graph['nodes'][j]
        assert Counter(map(tuple, saved[2])) == Counter(map(tuple, edges))
        assert all(p < j for p, _, _, _ in edges)
        bounds = [(earliest_values[p] if p >= 0 else 0)+s+d for p, s, d, _ in saved[2]]
        minimum = max(bounds)
        gap = event[0]-minimum
        assert gap >= 0 and saved[1] == gap
        earliest_values.append(minimum+gap)
        ties = [k for k, value in enumerate(bounds) if value == minimum]
        assert graph['max_parent_edge_indices'][j] == ties
        expected_ties.append(ties)
        propagation_min.append(min((propagation_min[saved[2][k][0]] if saved[2][k][0] >= 0 else 0)+saved[2][k][2]
                                   for k in ties))
        propagation_max.append(max((propagation_max[saved[2][k][0]] if saved[2][k][0] >= 0 else 0)+saved[2][k][2]
                                   for k in ties))
        port_service[port] += serialization[i]
        if gap:
            all_gaps['source_boundary' if event[1] in endpoint and event[6] == 17 else 'dispatch_boundary'] += gap
    terminal = sorted(value for head, value in packet_terminal.items()
                      if head[3] == 252 and value[3] == result['finish_ns'])
    assert terminal and graph['terminal_ties'] == [list(v) for v in terminal]
    critical = set()
    pending = {r[0] for r in terminal}
    while pending:
        current = pending.pop()
        if current < 0 or current in critical:
            continue
        critical.add(current)
        pending.update(graph['nodes'][current][2][k][0] for k in expected_ties[current])
    assert graph['critical_tx'] == sorted(critical)
    parts = Counter(serialization_ns=terminal[0][1], propagation_ns=terminal[0][2],
                    source_boundary_gap_ns=0, dispatch_boundary_gap_ns=0)
    current = terminal[0][0]
    for node, edge in graph['witness']:
        assert node == current and edge in expected_ties[node]
        assert edge == min(expected_ties[node], key=lambda k: (graph['nodes'][node][2][k][0], graph['nodes'][node][2][k][3]))
        source = ev[transmissions[node]]
        parts['source_boundary_gap_ns' if source[1] in endpoint and source[6] == 17 else 'dispatch_boundary_gap_ns'] += graph['nodes'][node][1]
        parent, s, d, _ = graph['nodes'][node][2][edge]
        parts['serialization_ns'] += s
        parts['propagation_ns'] += d
        current = parent
    assert current == -1 and sum(parts.values()) == result['finish_ns']
    summary = graph['summary']
    assert dict(parts) == summary['witness'] and summary['finish_ns'] == result['finish_ns']
    assert summary['max_port_service_ns'] == max(port_service.values())
    assert summary['finish_minus_max_port_service_ns'] == result['finish_ns']-max(port_service.values())
    assert summary['all_tx_boundary_gap_sums_ns'] == dict(all_gaps)
    assert summary['physical_tx'] == len(transmissions) and summary['source_data_packets'] == len(source_by_uid)
    assert summary['ack_packets'] == len(ack_links) == len(source_by_uid)
    assert summary['maximum_predecessor_ties'] == sum(len(t)>1 for t in expected_ties)
    assert summary['critical_path_propagation_range_ns'] == [min(propagation_min[t[0]]+t[2] for t in terminal),
                                                            max(propagation_max[t[0]]+t[2] for t in terminal)]
    assert summary['critical_transmissions'] == len(critical) and summary['witness_transmissions'] == len(graph['witness'])
    for name, is_source in [('critical_source_gap_ns', True), ('critical_dispatch_gap_ns', False)]:
        assert summary[name] == sum(graph['nodes'][j][1] for j in critical
                                   if (ev[transmissions[j]][1] in endpoint and ev[transmissions[j]][6] == 17) == is_source)
    # Verify per-work completion using exact QP header, not endpoint/seq candidates.
    packet_prefix = {}
    for head, path in hops.items():
        if head[3] == 17:
            first = ev[path[0]]
            packet_prefix[head[4], first[7]+first[11]] = packet_terminal[head][3]
    ack_prefix = {}
    for head, value in packet_terminal.items():
        if head[3] == 252:
            seq = ev[transmissions[value[0]]][7]
            ack_prefix[head[5], seq] = value[3]
    for work in result['persistent_transport']['work']:
        port = streams[work['stream']]['wire_port']
        assert work['accepted_ns'] == packet_prefix[port, work['end']]
        assert work['acked_ns'] == ack_prefix[port, work['end']]
    return dict(data_packets=len(source_by_uid), ack_packets=len(ack_links), physical_tx=len(transmissions),
                work_completions=len(result['persistent_transport']['work']), critical_tx=len(critical),
                finish_ns=result['finish_ns'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'network', 'analysis', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    config = load(args.inputs/'observed/config.json')
    hashes = load(args.inputs/'reference-sha256.json')
    rows, mutations = [], []
    summaries = load(args.analysis/'conditions.json')
    for rate in config['upper_rates']:
        topology = load(args.network/f'topology-{rate}/topology.json')
        for policy in config['policies']:
            name = f'{rate}-{policy}'
            reference_path = args.inputs/'reference-network'/name/'result.json'
            assert hashlib.sha256(reference_path.read_bytes()).hexdigest() == hashes[name]
            result = load(args.network/name/'result.json')
            reference = load(reference_path)
            plain = deepcopy(result)
            del plain['packet_capture']['wire_headers']
            del plain['packet_capture']['selection']['wire_headers']
            assert plain == reference
            plan = load(args.inputs/'plans'/(policy+'.json'))
            graph = load(args.analysis/(name+'-graph.json'))
            assert next(r for r in summaries if r['condition'] == name) == dict(condition=name, original_fields_equal=True, **graph['summary'])
            rows.append(dict(condition=name, original_fields_equal=True, **verify(result, topology, plan, graph)))
            if not mutations:
                for kind in ('ack_port', 'propagation_edge', 'gap'):
                    bad_result, bad_graph = deepcopy(result), deepcopy(graph)
                    if kind == 'ack_port':
                        header = next(h for h in bad_result['packet_capture']['wire_headers']['headers'] if h[3] == 252)
                        header[5] += 1
                    elif kind == 'propagation_edge':
                        edge = next(e for n in bad_graph['nodes'] for e in n[2] if e[3] == 'arrival')
                        edge[2] += 1
                    else:
                        bad_graph['nodes'][0][1] += 1
                    try:
                        verify(bad_result, topology, plan, bad_graph)
                    except (AssertionError, KeyError):
                        mutations.append(kind)
                    else:
                        raise AssertionError('checker accepted corruption: '+kind)
    receipt = dict(passed=True, conditions=rows, rejected_corruptions=mutations,
                   total_data_packets=sum(r['data_packets'] for r in rows),
                   total_ack_packets=sum(r['ack_packets'] for r in rows),
                   total_physical_tx=sum(r['physical_tx'] for r in rows),
                   total_work_completions=sum(r['work_completions'] for r in rows),
                   counterfactual_performance_verified=False, hardware_calibrated=False)
    args.output.write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
