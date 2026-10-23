"""Explain six fixed executions from physical packet order; no counterfactual model."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def prepare(previous, out):
    assert not out.exists()
    out.mkdir()
    shutil.copytree(previous/'input/observed', out/'observed')
    shutil.copytree(previous/'plans', out/'plans')
    protocol = read(out/'observed/protocol.json')
    assert 'wire_headers' not in protocol['packet_capture']
    protocol['packet_capture']['wire_headers'] = True
    save(out/'observed/protocol.json', protocol)
    references = out/'reference-network'
    references.mkdir()
    cfg = read(out/'observed/config.json')
    identities = {}
    for rate in cfg['upper_rates']:
        for policy in cfg['policies']:
            name = f'{rate}-{policy}'
            target = references/name
            target.mkdir()
            source = previous/'network'/name/'result.json'
            shutil.copy2(source, target/'result.json')
            identities[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    save(out/'reference-sha256.json', identities)


def duration(size, rate):
    return (2*size*8*10**9 + rate)//(2*rate)


def capture_source(repo, out):
    assert not out.exists()
    out.mkdir()
    native = repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    commit = '3964b1d41cb5f7b51004c316b0eae1a3713c2498'
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=native, text=True).strip() == commit
    records = []
    for name, needles in [
        ('src/network/utils/data-rate.cc', ['return Seconds(int64x64_t(bits) / m_bps);']),
        ('src/core/model/nstime.h', [': m_data(v.Round())']),
        ('src/point-to-point/model/qbb-net-device.cc', ['m_phyTxBeginTrace(m_currentPkt);', 'Time txTime = m_bps.CalculateBytesTxTime(p->GetSize());']),
        ('src/point-to-point/model/qbb-channel.cc', ['txTime + m_delay']),
        ('src/point-to-point/model/rdma-hw.cc', ['seqh.SetSport(ch.udp.dport);', 'seqh.SetDport(ch.udp.sport);', 'head.SetProtocol(x == 1 ? 0xFC : 0xFD);'])]:
        raw = (native/name).read_bytes()
        assert raw == subprocess.check_output(['git', 'show', commit+':'+name], cwd=native)
        lines = raw.decode().splitlines()
        excerpts = []
        for needle in needles:
            line = next(i for i, value in enumerate(lines) if needle in value)
            excerpts.append(dict(needle=needle, first_line=max(0,line-2)+1, lines=lines[max(0,line-2):line+4]))
        target = out/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        records.append(dict(path=name, commit=commit, sha256=hashlib.sha256(raw).hexdigest(), excerpts=excerpts))
    save(out/'sources.json', records)


def reconstruct(result, topology):
    capture = result['packet_capture']
    assert capture['complete'] and not capture['omitted_events']
    events = capture['events']
    header_table = capture['wire_headers']
    headers, indices = header_table['headers'], header_table['event_header_indices']
    assert len(events) == len(indices)
    gpu = {n['id'] for n in topology['nodes'] if n['kind'] == 'gpu'}
    ports = {}
    for link in topology['links']:
        a, b = (link['src'], link['src_port']), (link['dst'], link['dst_port'])
        ports[a] = (b, link['rate_bps'], link['delay_ns'])
        ports[b] = (a, link['rate_bps'], link['delay_ns'])
    packet = defaultdict(list)
    for i, e in enumerate(events):
        h = headers[indices[i]]
        assert (h[0], h[3]) == (e[4], e[6]) and h[6] == 1
        assert e[6] in (17, 252) and e[3] != 2
        packet[tuple(h)].append(i)
    tx_events = [i for i, e in enumerate(events) if e[3] == 0]
    tx_id = {event: j for j, event in enumerate(tx_events)}
    # A node is one physical TX, with candidates (predecessor TX, serialization,
    # propagation, kind). Root -1 is application data already published at 0.
    incoming = defaultdict(list)
    data_ends = {}
    ack_starts = []
    terminals = {}
    packet_count = Counter()
    for key, selected in packet.items():
        sent = [i for i in selected if events[i][3] == 0]
        received = [i for i in selected if events[i][3] == 1]
        assert sent and len(sent) == len(received)
        assert events[sent[0]][1] in gpu
        by_rx_port = {(events[i][1], events[i][2]): i for i in received}
        assert len(by_rx_port) == len(received)
        for k, i in enumerate(sent):
            e = events[i]
            peer, rate, delay = ports[e[1], e[2]]
            serial = duration(e[8], rate)
            rx = events[by_rx_port[peer]]
            assert rx[0] == e[0] + serial + delay
            assert (rx[6], rx[7], rx[8], rx[11]) == (e[6], e[7], e[8], e[11])
            if k+1 < len(sent):
                following = sent[k+1]
                assert events[following][1] == peer[0]
                incoming[tx_id[following]].append([tx_id[i], serial, delay, 'arrival'])
            else:
                assert peer[0] in gpu
                terminals[key] = (tx_id[i], serial, delay, rx[0], by_rx_port[peer])
        first, last = sent[0], sent[-1]
        first_e, last_e = events[first], events[last]
        packet_count[first_e[6]] += 1
        if first_e[6] == 17:
            assert last_e[11] > 0
            # Cumulative ACK prefix, exact native reverse IPv4/ports.
            _, sip, dip, _, sport, dport, _ = key
            identity = (dip, sip, dport, sport, last_e[7] + last_e[11])
            assert identity not in data_ends
            data_ends[identity] = terminals[key]
            incoming[tx_id[first]].append([-1, 0, 0, 'published'])
        else:
            ack_starts.append((key, first, first_e[7]))
    assert packet_count[17] == packet_count[252]
    ack_matches = []
    for key, first, sequence in ack_starts:
        _, sip, dip, _, sport, dport, _ = key
        trigger = data_ends.pop((sip, dip, sport, dport, sequence))
        parent, serial, delay, arrival, rx_event = trigger
        assert arrival <= events[first][0]
        incoming[tx_id[first]].append([parent, serial, delay, 'ack_trigger'])
        ack_matches.append([first, rx_event])
    assert not data_ends
    previous_port = {}
    services = Counter()
    nodes = []
    max_parents = []
    propagation_min, propagation_max = [], []
    gaps = Counter()
    for j, i in enumerate(tx_events):
        e = events[i]
        port = e[1], e[2]
        _, rate, _ = ports[port]
        serial = duration(e[8], rate)
        if port in previous_port:
            parent, previous_serial = previous_port[port]
            incoming[j].append([parent, previous_serial, 0, 'port'])
        previous_port[port] = j, serial
        edges = incoming[j]
        assert edges and all(parent < j for parent, _, _, _ in edges)
        arrivals = [(events[tx_events[parent]][0] if parent >= 0 else 0)+s+d
                    for parent, s, d, _ in edges]
        earliest = max(arrivals)
        gap = e[0] - earliest
        assert gap >= 0
        boundary = 'source_boundary' if e[1] in gpu and e[6] == 17 else 'dispatch_boundary'
        if gap:
            gaps[boundary] += gap
        nodes.append([i, gap, edges])
        max_parents.append([k for k, value in enumerate(arrivals) if value == earliest])
        propagation_min.append(min((propagation_min[edges[k][0]] if edges[k][0] >= 0 else 0)+edges[k][2]
                                   for k in max_parents[-1]))
        propagation_max.append(max((propagation_max[edges[k][0]] if edges[k][0] >= 0 else 0)+edges[k][2]
                                   for k in max_parents[-1]))
        services[port] += serial
    end_candidates = [(value, key) for key, value in terminals.items() if key[3] == 252]
    finish = max(v[0][3] for v in end_candidates)
    assert finish == result['finish_ns']
    end_ties = sorted(v[0] for v in end_candidates if v[0][3] == finish)
    critical = set()
    stack = [v[0] for v in end_ties]
    while stack:
        j = stack.pop()
        if j < 0 or j in critical:
            continue
        critical.add(j)
        stack.extend(nodes[j][2][k][0] for k in max_parents[j])
    # Canonical additive witness: smallest terminal TX, then smallest parent TX
    # among ties; retain the full critical subgraph separately.
    terminal = end_ties[0]
    total = Counter(serialization_ns=terminal[1], propagation_ns=terminal[2],
                    source_boundary_gap_ns=0, dispatch_boundary_gap_ns=0)
    witness = []
    current = terminal[0]
    while current >= 0:
        i, gap, edges = nodes[current]
        field = 'source_boundary_gap_ns' if events[i][1] in gpu and events[i][6] == 17 else 'dispatch_boundary_gap_ns'
        total[field] += gap
        selected = min(max_parents[current], key=lambda k: (edges[k][0], edges[k][3]))
        parent, serial, delay, kind = edges[selected]
        total['serialization_ns'] += serial
        total['propagation_ns'] += delay
        witness.append([current, selected])
        current = parent
    assert sum(total.values()) == finish
    summary = dict(finish_ns=finish, max_port_service_ns=max(services.values()),
                   finish_minus_max_port_service_ns=finish-max(services.values()),
                   witness=dict(total), witness_transmissions=len(witness),
                   critical_path_propagation_range_ns=[min(propagation_min[t[0]]+t[2] for t in end_ties),
                                                       max(propagation_max[t[0]]+t[2] for t in end_ties)],
                   critical_transmissions=len(critical), maximum_predecessor_ties=sum(len(x)>1 for x in max_parents),
                   all_tx_boundary_gap_sums_ns=dict(gaps),
                   critical_source_gap_ns=sum(nodes[j][1] for j in critical
                                              if events[nodes[j][0]][1] in gpu and events[nodes[j][0]][6] == 17),
                   critical_dispatch_gap_ns=sum(nodes[j][1] for j in critical
                                                if not (events[nodes[j][0]][1] in gpu and events[nodes[j][0]][6] == 17)),
                   source_data_packets=packet_count[17], ack_packets=packet_count[252], physical_tx=len(nodes))
    return dict(node_columns=['capture_event', 'actual_minus_max_predecessor_ns', 'predecessors'],
                edge_columns=['parent_tx', 'serialization_ns', 'propagation_ns', 'kind'],
                root=-1, nodes=nodes, max_parent_edge_indices=max_parents,
                terminal_ties=end_ties, critical_tx=sorted(critical), witness=witness,
                ack_matches=sorted(ack_matches), summary=summary)


def analyze(inputs, network, out):
    assert not out.exists()
    out.mkdir()
    config = read(inputs/'observed/config.json')
    rows = []
    for rate in config['upper_rates']:
        topology = read(network/f'topology-{rate}/topology.json')
        for policy in config['policies']:
            name = f'{rate}-{policy}'
            result = read(network/name/'result.json')
            reference = read(inputs/'reference-network'/name/'result.json')
            stripped = deepcopy(result)
            del stripped['packet_capture']['wire_headers']
            del stripped['packet_capture']['selection']['wire_headers']
            assert stripped == reference, name
            graph = reconstruct(result, topology)
            (out/(name+'-graph.json')).write_text(json.dumps(graph, ensure_ascii=False, separators=(',', ':')) + '\n')
            rows.append(dict(condition=name, original_fields_equal=True, **graph['summary']))
    save(out/'conditions.json', rows)
    save(out/'scope.json', dict(observed_execution_accounting=True, counterfactual_prediction=False,
         independent_performance_samples=False, hardware_calibrated=False, novel_method_claimed=False,
         boundary_gaps_are_removable_loss=False, sum_of_all_gaps_is_elapsed_time=False))
    print(json.dumps(rows))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--previous-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('analyze')
    for name in ('inputs', 'network', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p = sub.add_parser('capture-source')
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.previous_root, args.output)
    elif args.action == 'capture-source':
        capture_source(args.repo, args.output)
    else:
        analyze(args.inputs, args.network, args.output)
