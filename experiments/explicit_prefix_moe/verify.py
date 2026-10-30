"""Independently reconcile raw physical hops, source ACKs and explicit repair."""
import argparse
from collections import Counter, defaultdict, deque
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def files(d):
    return {str(p.relative_to(d)): sha(p) for p in sorted(d.rglob('*')) if p.is_file()}


def normalized(c):
    c = deepcopy(c)
    c['route_replay']['manifest'] = '<verified contents>'
    return c


def check_case(d, expected, frozen, original, original_routes):
    r = load(d/'result.json')
    assert sha(d/'result.json') == expected['result_sha256']
    assert r['passed'] and r['drained'] and load(d/'verification.json')['passed']
    assert (r['gpu_count'], r['switch_count']) == (128, 20)
    c = load(d/'input.json')
    assert normalized(c) == normalized(original)
    ref = c['route_replay']['manifest']
    assert sha(d/ref['path']) == ref['sha256']
    routes = load(d/ref['path'])
    for row in routes['files']:
        assert sha(d/row['file']['path']) == row['file']['sha256']
        row['file']['path'] = '<verified contents>'
    assert routes == original_routes and len(routes['files']) == 15
    assert files(d/'graphs') == frozen['graph_sha256']
    base = Path(frozen['reference'])/'normal'
    assert load(d/'topology.json') == load(base/'topology.json')
    protocol = load(d/'rdma.json')
    name = d.name
    if name != 'normal':
        window = 20000 if name.endswith('w20') else 60000
        delay = 0 if name.startswith('instant') else 60000
        schedule = dict(mode='switch_links/2', events=[dict(src=128, dst=132, time_ns=4000,
            route_update_delay_ns=delay, restore_time_ns=4000+window,
            restore_route_update_delay_ns=delay)])
        assert protocol.pop('link_failures') == schedule
        mode = protocol.pop('local_failover', None)
        assert mode == (dict(mode='explicit_prefix/1', max_events=100000) if name.startswith('prefix')
                        else dict(mode='live_ecmp/1') if name.startswith('local') else None)
        failure = r['link_failures']
        assert failure['configuration'] == schedule and failure['pending_events'] == 0
        assert failure['route_epoch'] == 2
        kinds = Counter(x['kind'] for x in failure['events'])
        assert kinds == Counter(link_down=1, link_up=1, routes_updated=2)
        for x in failure['events']:
            if x['kind'] == 'link_down':
                assert x['time_ns'] == 4000
            elif x['kind'] == 'link_up':
                assert x['time_ns'] == 4000+window
            else:
                version = x['notification_revision']
                assert version in (1, 2)
                assert x['time_ns'] == (4000 if version == 1 else 4000+window)+delay
                assert x['known_state'] == ('down' if version == 1 else 'up')
    assert protocol == load(base/'rdma.json')
    cap = r['packet_capture']
    assert cap['complete'] and cap['omitted_events'] == 0
    assert cap['observations'] == len(cap['events'])
    assert len(r['queue_events']) == r['queue_observations']
    codes = cap['event_codes']
    packets = [dict(zip(cap['columns'], x)) for x in cap['events']]
    repair = r.get('explicit_repair')
    metadata = repair['packet_metadata'] if repair else [[0, x['ip_protocol']] for x in packets]
    if repair:
        assert repair['complete'] and repair['observations'] == len(repair['events'])
        assert len(metadata) == len(packets)
    ports = {}
    for e in load(d/'topology.json')['links']:
        ports[e['src'], e['src_port']] = e['dst'], e['dst_port'], e['rate_bps'], e['delay_ns']
        ports[e['dst'], e['dst_port']] = e['src'], e['src_port'], e['rate_bps'], e['delay_ns']
    def identity(x, meta):
        return (x['packet_uid'], x['message_id'], x['ip_protocol'], x['sequence'],
                x['model_bytes'], x['data_payload_bytes'], *meta)
    arrivals = defaultdict(deque)
    for x, meta in zip(packets, metadata):
        if x['event'] in (codes['rx'], codes['drop']):
            arrivals[x['node'], x['port'], *identity(x, meta)].append(x['time_ns'])
    busy, actual_source, wire = defaultdict(int), Counter(), Counter()
    hops = wrapped_bytes = 0
    wrapped_hops = Counter()
    feedback = defaultdict(list)
    for x, meta in zip(packets, metadata):
        if meta[0]:
            assert meta == [38, 250] and x['node'] in (132, 129, 133)
            if x['ip_protocol'] == 17:
                assert x['model_bytes'] == x['data_payload_bytes']+36+38
        else:
            assert meta[1] == x['ip_protocol']
        if x['event'] == codes['rx'] and x['node'] < 128 and x['ip_protocol'] == 252:
            feedback[x['node'], x['message_id'], x['time_ns']].append(x['sequence'])
        if x['event'] != codes['tx']:
            continue
        peer, peer_port, rate, delay = ports[x['node'], x['port']]
        key = (peer, peer_port, *identity(x, meta))
        assert arrivals[key], ('TX without RX/drop', name, x)
        received_ns = arrivals[key].popleft()
        duration = x['model_bytes']*8*10**9/rate
        assert abs(received_ns-x['time_ns']-delay-duration) <= 1
        assert x['time_ns'] >= busy[x['node'], x['port']]
        busy[x['node'], x['port']] = x['time_ns']+round(duration)
        wire[x['node'], x['port']] += x['model_bytes']
        hops += 1
        wrapped_bytes += meta[0]
        if meta[0]:
            assert (x['node'], peer) in ((132, 129), (129, 133))
            wrapped_hops[x['node'], peer, x['packet_uid'], x['ip_protocol']] += 1
        if x['node'] < 128 and x['ip_protocol'] == 17:
            assert not meta[0]
            actual_source[x['time_ns'], x['message_id'], x['node'], x['sequence'],
                          x['data_payload_bytes'], x['packet_uid']] += 1
    assert not any(arrivals.values()), 'RX/drop without physical TX'
    for port in r['ports']:
        assert wire[port['node'], port['port']] == port['model_tx_bytes']
    assert sum(wire.values()) == expected['model_wire_bytes']
    assert wrapped_bytes == expected['encapsulation_wire_bytes']
    if repair:
        events = [dict(zip(repair['columns'], row)) for row in repair['events']]
        stages = {i: Counter() for i in (1, 2, 3)}
        for x in events:
            stage = x['action']
            assert stage in stages and x['node'] == {1: 132, 2: 129, 3: 133}[stage]
            # Merge records removed overhead, while model_bytes is already inner.
            assert x['merge'] == 133 and x['encapsulation_bytes'] == 38
            stages[stage][x['packet_uid'], x['inner_ip_protocol']] += 1
        assert stages[1] == stages[2] == stages[3] and stages[1]
        for key, count in stages[1].items():
            assert wrapped_hops[132, 129, *key] == wrapped_hops[129, 133, *key] == count
        assert wrapped_bytes == sum(stages[1].values())*2*38
    n = r['service_transport']['native']
    expected_source, high = Counter(), defaultdict(int)
    total = 0
    for row in n['source_packets']:
        x = dict(zip(n['source_packet_columns'], row))
        expected_source[x['time_ns'], x['stream'], x['src'], x['sequence'], x['payload_bytes'], x['packet_uid']] += 1
        assert x['sequence'] <= high[x['stream']]
        high[x['stream']] = max(high[x['stream']], x['sequence']+x['payload_bytes'])
        total += x['payload_bytes']
    assert expected_source == actual_source
    messages = r['service_transport']['messages']
    logical = sum(m['bytes'] for m in messages)
    assert logical == sum(high.values()) == expected['logical_payload_bytes']
    assert total == expected['source_payload_bytes']
    assert total-logical == expected['retransmitted_payload_bytes']
    for m in messages:
        assert m['ready_ns'] <= m['published_ns'] <= m['receive_ready_ns'] <= m['ack_ns']
        assert any(s >= m['end'] for s in feedback[m['src'], m['stream'], m['ack_ns']]), ('missing source ACK', m)
    assert n['recovery']['complete'] and not n['recovery']['failed']
    assert n['closed'] and n['physical_drained'] and n['inflight_packets'] == 0
    assert n['message_payload']['retained_bytes'] == r['service_transport']['message_buffers']['used_bytes'] == 0
    assert not r['service']['inflight_helpers']['attempts']
    for stream in n['streams']:
        assert stream['qp_creations'] <= 1
        assert stream['snd_una'] == stream['submitted_bytes'] == stream['published_bytes']
    assert expected['queue_peak_bytes'] == max(x[3] for x in r['queue_events'])
    for g in r['service']['phase_completion']['gates']:
        assert g['released_ns'] == max(x['arrived_ns'] for x in g['members'])
    history = load(d/'frontend.json')['history']
    assert expected['group_complete_ns'] == {str(i): max(h['finished_ns'] for h in history if h['group_index'] == i) for i in range(4)}
    return dict(physical_hops=hops, messages=len(messages), queue_rows=len(r['queue_events']),
                graph_files=len(files(d/'graphs')), actual_source_ACK_witnesses=len(messages),
                encapsulation_wire_bytes=wrapped_bytes)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('study', 'analysis', 'output'):
        p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args()
    f, analysis = load(a.study/'frozen.json'), load(a.analysis)
    assert sha(a.study/'frozen.json') == analysis['frozen_sha256']
    root = Path(__file__).resolve().parents[2]
    for group in ('compiled_sources_sha256', 'source_sha256'):
        for path, h in f[group].items():
            assert sha(root/path) == h
    for group in ('routes_sha256', 'libraries_sha256'):
        for path, h in f[group].items():
            assert sha(path) == h
    assert sha(f['backend']) == f['backend_sha256']
    original = load(Path(f['reference'])/'normal/input.json')
    ref = original['route_replay']['manifest']
    routes = load(Path(f['reference'])/'normal'/ref['path'])
    for row in routes['files']:
        row['file']['path'] = '<verified contents>'
    checks = {}
    for name, expected in analysis['cases'].items():
        d = Path(expected['directory'])
        checks[name] = check_case(d, expected, f, original, routes)
        gc.collect()
    for window, effect in analysis['effects'].items():
        local, prefix = (analysis['cases'][k+'-w'+window] for k in ('local', 'prefix'))
        saved = local['max_group_complete_ns']-prefix['max_group_complete_ns']
        assert effect['saved_ns'] == saved
        assert effect['relative_local_percent'] == 100*saved/local['max_group_complete_ns']
        assert effect['group_saved_ns'] == {str(i): local['group_complete_ns'][str(i)]-prefix['group_complete_ns'][str(i)] for i in range(4)}
    with a.output.open('x') as s:
        json.dump(dict(passed=True, new_Clos128_networks=4, reused_conditions=5, per_case=checks,
            analysis_sha256=sha(a.analysis), verifier_sha256=sha(Path(__file__)),
            coverage='all physical protocols, size and serialization; repair DATA/ACK/CNP hops; source DATA and ACK identity; no unobserved CNP ECMP hash claim'), s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, physical_hops=sum(x['physical_hops'] for x in checks.values()),
                         source_ACK_witnesses=sum(x['messages'] for x in checks.values()))))


if __name__ == '__main__':
    main()
