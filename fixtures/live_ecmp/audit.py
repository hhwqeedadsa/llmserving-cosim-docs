"""Independent shortest-path, hash, local-state and actual packet audit."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import json
from pathlib import Path

from fixtures.selective_repeat.audit import audit as transport_audit


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def murmur(words, seed):
    mask = 2**32-1; h = seed
    def rot(v, n): return ((v << n)|(v >> (32-n))) & mask
    for word in words:
        k = word*0xcc9e2d51 & mask; k = rot(k, 15)*0x1b873593 & mask
        h = rot(h ^ k, 13); h = (h*5+0xe6546b64) & mask
    h ^= 12; h ^= h >> 16; h = h*0x85ebca6b & mask; h ^= h >> 13
    h = h*0xc2b2ae35 & mask; h ^= h >> 16
    return h


def audit(topology, protocol, work, result):
    report = transport_audit(result, work, protocol)
    nodes = {v['id']: v for v in topology['nodes']}; gpus = result['gpu_count']
    adjacency = defaultdict(list); edge_ports = {}
    for link in topology['links']:
        a, b = link['src'], link['dst']; ap, bp = link['src_port'], link['dst_port']
        adjacency[a].append((b, ap)); adjacency[b].append((a, bp)); edge_ports[a, b] = (ap, bp)
    lf = result['link_failures']; events = lf['events']; live = 'local_failover' in protocol
    assert ('local_failover' in lf) == live and lf['configuration'] == protocol['link_failures']
    downs = [v for v in events if v['kind'] == 'link_down']; updates = [v for v in events if v['kind'] == 'routes_updated']
    assert lf['pending_events'] == 0 and lf['route_epoch'] == len(downs) == len(updates)
    for epoch, update in enumerate(updates, 1):
        assert update['route_epoch'] == epoch and update['excluded_directed_ports'] == 2*epoch
    for i, declared in enumerate(protocol['link_failures']['events']):
        actual = next(v for v in downs if v['event_index'] == i)
        assert actual['time_ns'] == declared['time_ns'] and (actual['src'], actual['dst']) == (declared['src'], declared['dst'])
        u = next(v for v in updates if v['event_index'] == i)
        assert u['time_ns'] == actual['time_ns']+declared['route_update_delay_ns']
    def failed(visible):
        pairs = set()
        for v in visible:
            d = next(d for d in downs if d['event_index'] == v['event_index'])
            pairs.update(((d['src'], d['dst']), (d['dst'], d['src'])))
        return pairs
    cache = {}
    def candidates(node, dst, visible, physically_down):
        dead = failed(visible); key = dst, nodes[node]['plane'], tuple(sorted(dead))
        if key not in cache:
            distance = {dst: 0}; q = deque([dst]); plane = nodes[node]['plane']
            while q:
                here = q.popleft()
                for peer, _ in adjacency[here]:
                    if (here, peer) in dead or (peer >= gpus and nodes[peer]['plane'] != plane): continue
                    if peer not in distance:
                        distance[peer] = distance[here]+1
                        if peer >= gpus: q.append(peer)
            cache[key] = distance
        distance = cache[key]
        return [port for peer, port in adjacency[node] if (node, peer) not in dead and
                distance.get(peer, -100) == distance.get(node, -200)-1 and
                (not live or (node, peer) not in failed(physically_down))]
    if live:
        assert lf['local_failover']['configuration'] == protocol['local_failover']
        records = lf['local_failover']['events']; assert len(records) == 2*len(downs)
        for row in records:
            event = next(v for v in downs if v['event_index'] == row['event_index']); idx = events.index(event)
            visible = [v for v in events[:idx] if v['kind'] == 'routes_updated']
            physical = [v for v in events[:idx+1] if v['kind'] == 'link_down']; node = row['node']
            assert row['time_ns'] == event['time_ns'] and node in (event['src'], event['dst'])
            # Reconstruct old global candidates and apply only this node's own
            # physically down ports, preserving the native candidate order.
            removed = Counter(); before_count = after_count = empty = destinations = 0
            for dst in range(gpus):
                before = candidates(node, dst, visible, [])
                after = candidates(node, dst, visible, physical)
                if before: destinations += 1; empty += not after
                before_count += len(before); after_count += len(after); removed.update(set(before)-set(after))
            assert row['candidate_destinations'] == destinations and row['candidate_next_hops'] == before_count
            assert row['installed_next_hops'] == after_count and row['empty_destinations'] == empty
            assert row['removed_by_port'] == [dict(port=p, destination_entries=n) for p, n in sorted(removed.items())]
    capture = result['packet_capture']; wh = capture['wire_headers']; headers = [dict(zip(wh['columns'], h)) for h in wh['headers']]
    queued = 0; discarded = Counter(); late_good = Counter(); arrivals = {}
    for raw, hi in zip(capture['events'], wh['event_header_indices']):
        e = dict(zip(capture['columns'], raw)); h = headers[hi]; node = e['node']; now = e['time_ns']; proto = e['ip_protocol']
        assert h['packet_uid'] == e['packet_uid'] and h['ip_protocol'] == proto
        if node < gpus or proto not in (17, 252, 253): continue
        if e['event'] == 1: arrivals[node, e['packet_uid'], proto, e['sequence']] = e
        visible = [v for v in updates if v['time_ns'] <= now]; physical = [v for v in downs if v['time_ns'] <= now]
        dst = (h['ip_dst']-0x0b000001)//32
        options = candidates(node, dst, visible, physical)
        selection = options[murmur([h['ip_src'], h['ip_dst'], h['source_port']|(h['destination_port'] << 16)], protocol['seed']+node) % len(options)] if options else None
        if e['event'] == 3:
            assert e['port'] == selection, 'actual enqueue differs from native hash of allowed candidates'
            queued += 1
            # Physical TX later than convergence need not be newly selected:
            # old queued packets retain the decision made at enqueue.
            if physical and not visible: late_good[node] += 1
            if node == 8 and 6000 < now < 16000 and physical: late_good['pending8_after_other_update'] += 1
        if e['event'] == 5:
            closing = any(now == d['time_ns'] and node in (d['src'], d['dst']) and
                e['port'] == (d['src_port'] if node == d['src'] else d['dst_port']) for d in downs)
            if closing: discarded['queue_close'] += 1
            else:
                arrival = arrivals[node, e['packet_uid'], proto, e['sequence']]
                assert arrival['port'] == e['port'] and arrival['time_ns'] == now, 'local forwarding drop uses ingress port'
                if live: assert selection is None, 'live local candidates must be usable'
                else:
                    out_peer = next(peer for peer, p in adjacency[node] if p == selection) if selection is not None else None
                    assert selection is None or (node, out_peer) in failed(physical)
                discarded['no_live_old_candidate' if live else 'stale_forwarding'] += 1
    report.update(local_policy=live, enqueues_hash_verified=queued, local_drops=dict(discarded),
        preconvergence_live_forwarding={str(k): v for k, v in late_good.items()},
        route_updates=[dict(time_ns=v['time_ns'], epoch=v['route_epoch'], excluded_directed_ports=v['excluded_directed_ports']) for v in updates])
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--stage', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); s = a.stage; assert not a.output.exists(); topo = load(s/'topology/topology.json'); inputs = s/load(s/'run.json')['inputs_directory']; work = load(inputs/'work.json')
    results = {}
    protocols = {'default-old': 'default.json', 'default-new': 'default.json', 'live': 'protocol.json', 'overlap': 'overlap.json', 'zero': 'zero.json'}
    for name, protocol in protocols.items(): results[name] = audit(topo, load(inputs/protocol), work, load(s/'network'/name/'result.json'))
    assert results['default-old'] == results['default-new']
    assert results['live']['preconvergence_live_forwarding'].get('8', 0) > 0
    assert results['overlap']['preconvergence_live_forwarding']['pending8_after_other_update'] > 0
    assert results['overlap']['route_updates'] == [dict(time_ns=6000, epoch=1, excluded_directed_ports=2), dict(time_ns=16000, epoch=2, excluded_directed_ports=4)]
    assert not results['zero']['local_drops'] and not results['zero']['physical'].get('physical_drop', 0)
    bad = deepcopy(load(s/'network/live/result.json')); bad['link_failures']['local_failover']['events'][0]['installed_next_hops'] += 1
    try: audit(topo, load(inputs/'protocol.json'), work, bad)
    except AssertionError: pass
    else: raise AssertionError('undetected candidate count mutation')
    save(a.output, dict(passed=True, cases=results, candidate_mutation_rejected=True, all_candidates_down_packet_coverage='reserved sixth fresh execution',
        scope='local failure functionality and old default compatibility; not calibrated performance or paper innovation'))


if __name__ == '__main__': main()
