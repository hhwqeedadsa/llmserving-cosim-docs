"""Independent physical-hop, notification-order, route and byte-state checks."""
import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path

from fixtures.selective_repeat.audit import audit as audit_transport
from fixtures.live_ecmp.audit import murmur


def load(p):
    return json.loads(Path(p).read_bytes())


def audit(topology, protocol, work, result):
    proof = audit_transport(result, work, protocol)
    assert result['gpu_count'] == 128 and result['switch_count'] == 20
    for s in result['persistent_transport']['streams']:
        assert s['qp_creations'] == 1 and s['snd_una'] == s['published_bytes'] == s['submitted_bytes']
    if 'link_failures' not in protocol:
        return dict(**proof, temporary=False)
    declared = protocol['link_failures']['events']
    lf = result['link_failures']
    assert lf['configuration'] == protocol['link_failures'] and lf['pending_events'] == 0
    assert len(declared) == 1  # This fixture's scenarios, not the platform limit.
    e = declared[0]
    assert e['src'] == 128 and e['dst'] == 132
    edge = next(x for x in topology['links'] if (x['src'], x['dst']) == (128, 132))
    selected = {(edge['src'], edge['src_port']), (edge['dst'], edge['dst_port'])}
    known_revision = epoch = 0
    known_dead = set()
    snapshots = []
    kinds = Counter()
    for event in lf['events']:
        kind = event['kind']
        kinds[kind] += 1
        assert event['event_index'] == 0
        if kind in ('link_down', 'link_up'):
            assert event['time_ns'] == e['time_ns' if kind == 'link_down' else 'restore_time_ns']
            assert (event['src'], event['dst']) == (128, 132)
            assert (event['src_port'], event['dst_port']) == (edge['src_port'], edge['dst_port'])
            assert event['route_epoch'] == epoch
        else:
            revision = event['notification_revision']
            assert revision in (1, 2)
            expected_time = (e['time_ns']+e['route_update_delay_ns'] if revision == 1 else
                             e['restore_time_ns']+e['restore_route_update_delay_ns'])
            assert event['time_ns'] == expected_time
            if revision > known_revision:
                assert kind == 'routes_updated'
                known_revision = revision
                known_dead = {(128, 132), (132, 128)} if revision == 1 else set()
                epoch += 1
                assert event['known_state'] == ('down' if revision == 1 else 'up')
            else:
                assert kind == 'routes_update_ignored' and event['known_revision'] == known_revision
            assert event['route_epoch'] == epoch and event['excluded_directed_ports'] == len(known_dead)
        snapshots.append((event['time_ns'], set(known_dead)))
    assert known_revision == 2 and not known_dead and epoch == lf['route_epoch']
    assert kinds['link_down'] == kinds['link_up'] == 1 and sum(kinds.values()) == 4
    assert result['finish_ns'] >= max(x['time_ns'] for x in lf['events'])
    local = 'local_failover' in protocol
    if local:
        local_events = lf['local_failover']['events']
        assert len(local_events) == 4
        assert {(x['time_ns'], x['node'], x['physical_state']) for x in local_events} == {
            (e[t], n, state) for t, state in [('time_ns', 'down'), ('restore_time_ns', 'up')] for n in (128, 132)}
    adjacency = defaultdict(list)
    nodes = {n['id']: n for n in topology['nodes']}
    for link in topology['links']:
        adjacency[link['src']].append((link['dst'], link['src_port']))
        adjacency[link['dst']].append((link['src'], link['dst_port']))
    cache = {}

    def candidates(node, dst, dead, physical_down):
        key = dst, tuple(sorted(dead))
        if key not in cache:
            distance = {dst: 0}
            q = deque([dst])
            while q:
                at = q.popleft()
                for peer, _ in adjacency[at]:
                    if (at, peer) in dead or (peer >= 128 and nodes[peer]['plane'] != 0):
                        continue
                    if peer not in distance:
                        distance[peer] = distance[at]+1
                        if peer >= 128:
                            q.append(peer)
            cache[key] = distance
        distance = cache[key]
        return [port for peer, port in adjacency[node] if (node, peer) not in dead and
                distance.get(peer, -100) == distance.get(node, -200)-1 and
                not (local and physical_down and (node, port) in selected)]

    cap = result['packet_capture']
    columns = cap['columns']
    wh = cap['wire_headers']
    headers = [dict(zip(wh['columns'], x)) for x in wh['headers']]
    counts = Counter()
    crossing = []
    wire_busy_until = defaultdict(int)
    rates = {(p['node'], p['port']): p['rate_bps'] for p in result['ports']}
    same_ns = {x['time_ns'] for x in lf['events']}
    for row, hi in zip(cap['events'], wh['event_header_indices']):
        p = dict(zip(columns, row))
        now, node, port, event = p['time_ns'], p['node'], p['port'], p['event']
        if event == cap['event_codes']['tx']:
            assert now >= wire_busy_until[node, port], 'overlapping physical transmissions after restore'
            wire_busy_until[node, port] = now+round(p['model_bytes']*8*10**9/rates[node, port])
            counts['serialized_transmissions_checked'] += 1
        if event == cap['event_codes']['tx'] and (node, port) in selected:
            assert not e['time_ns'] <= now < e['restore_time_ns'], 'new transmission on a closed port'
            counts['selected_port_tx'] += 1
            counts['selected_port_tx_after_restore'] += now >= e['restore_time_ns']
            duration = round(p['model_bytes']*8*10**9/edge['rate_bps'])
            if now < e['time_ns'] < e['restore_time_ns'] < now+duration:
                crossing.append(p)
        if event == cap['event_codes']['rx'] and (node, port) in selected:
            assert not e['time_ns'] <= now < e['restore_time_ns']
        if event != cap['event_codes']['beq_enqueue'] or node < 128 or p['ip_protocol'] not in (17, 252, 253):
            continue
        # Separate trace arrays do not encode ordering of zero-delay events at
        # one timestamp. Report these rows instead of inventing that ordering.
        if now in same_ns:
            counts['route_rows_at_event_boundary'] += 1
            continue
        visible = [state for t, state in snapshots if t < now]
        dead = visible[-1] if visible else set()
        choices = candidates(node, (headers[hi]['ip_dst']-0x0b000001)//32, dead,
                             e['time_ns'] <= now < e['restore_time_ns'])
        assert choices
        h = headers[hi]
        words = [h['ip_src'], h['ip_dst'], h['source_port'] | (h['destination_port'] << 16)]
        expected = choices[murmur(words, protocol['seed']+node) % len(choices)]
        assert expected == port, ('route differs from independently known state', p)
        counts['actual_enqueues_checked'] += 1
    assert counts['selected_port_tx_after_restore'] > 0
    arrived_uids = {x['packet_uid'] for x in result['persistent_transport']['selective_repeat']['events']
                    if x['kind'] == 'data_received'}
    assert all(x['packet_uid'] in arrived_uids for x in crossing)
    latest_ack = max(x['acked_ns'] for x in result['persistent_transport']['work'])
    return dict(**proof, temporary=True, notification_kinds=dict(kinds), route_epoch=epoch,
        routing=dict(counts), transmitting_across_short_outage=crossing,
        last_ack_ns=latest_ack, final_ns=result['finish_ns'],
        waited_for_future_notification=result['finish_ns'] > max(latest_ack, 100000))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    results = {}
    t = load(a.stage/'input/topology/topology.json')
    work = load(a.stage/'input/work.json')
    for name in ('normal-old', 'normal-new', 'stale-down', 'ordered-local', 'physical-before-report', 'inflight'):
        protocol = load(a.stage/'input'/('normal.json' if name.startswith('normal') else name+'.json'))
        results[name] = audit(t, protocol, work, load(a.stage/'network'/name/'result.json'))
    old, new = (load(a.stage/'network'/name/'result.json') for name in ('normal-old', 'normal-new'))
    for value in (old, new):
        del value['loaded_libraries']
    assert old == new, 'default raw network output changed'
    assert results['stale-down']['notification_kinds']['routes_update_ignored'] == 1
    assert results['stale-down']['route_epoch'] == 1 and results['stale-down']['waited_for_future_notification']
    assert results['physical-before-report']['route_epoch'] == 2
    assert results['inflight']['transmitting_across_short_outage']
    result = dict(passed=True, actual_Clos128_networks=6, default_result_identical_except_libraries=True,
                  cases=results, independent_route_scope='all DATA/ACK/NACK enqueues except same-ns state boundaries')
    with a.output.open('x') as s:
        json.dump(result, s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, cases=len(results), physical_transmissions=sum(
        v['physical']['tx'] for v in results.values()), route_enqueues=sum(
        v.get('routing', {}).get('actual_enqueues_checked', 0) for v in results.values()))))


if __name__ == '__main__':
    main()
