"""Independent checks of physical packets, source ACKs and recovery schedules."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('study', 'analysis', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    f, analysis = load(a.study/'frozen-v2.json'), load(a.analysis)
    assert sha(a.study/'frozen-v2.json') == analysis['frozen_sha256']
    assert len(f['conditions']) == len(analysis['cases']) == 7
    for group in ('compiled_sources_sha256', 'source_sha256'):
        for name, h in f[group].items():
            assert sha(root/name) == h, name
    for group in ('libraries_sha256', 'routes_sha256'):
        for name, h in f[group].items():
            assert sha(name) == h, name
    base = Path(f['normal'])
    for name, h in f['normal_sha256'].items():
        assert sha(base/name) == h
    old_config = load(base/'input.json')
    old_config['route_replay']['manifest'] = '<verified>'
    old_protocol = load(base/'rdma.json')
    original_manifest = None
    checks = {}
    for name, expected in analysis['cases'].items():
        d = a.study/name
        r = load(d/'result.json')
        assert r['passed'] and r['drained'] and load(d/'verification.json')['passed']
        assert sha(d/'result.json') == expected['result_sha256']
        assert (r['gpu_count'], r['switch_count']) == (128, 20)
        conf = load(d/'input.json')
        ref = conf['route_replay']['manifest']
        assert sha(d/ref['path']) == ref['sha256']
        manifest = load(d/ref['path'])
        assert len(manifest['files']) == 15
        for row in manifest['files']:
            assert sha(d/row['file']['path']) == row['file']['sha256']
            row['file']['path'] = '<verified-content>'
        if original_manifest is None:
            original_manifest = deepcopy(manifest)
        assert manifest == original_manifest
        conf['route_replay']['manifest'] = '<verified>'
        assert conf == old_config
        assert len(conf['route_replay']['requests']) == 15
        assert conf['transport']['transport_recovery']['initial_rto_ns'] == 17000
        assert conf['inflight_helpers']['policy'] == 'observe_only/1'
        graphs = {str(x.relative_to(d/'graphs')): sha(x) for x in (d/'graphs').rglob('*') if x.is_file()}
        assert graphs == f['normal_graphs_sha256']
        proto = load(d/'rdma.json')
        condition = f['conditions'][name]
        control_events = []
        restored_tx = 0
        if condition:
            local = proto.pop('local_failover', None)
            assert local == ({'mode': 'live_ecmp/1'} if condition['local'] else None)
            declared = dict(src=128, dst=132, time_ns=4000, route_update_delay_ns=condition['delay_ns'],
                restore_time_ns=4000+condition['window_ns'], restore_route_update_delay_ns=condition['delay_ns'])
            schedule = dict(mode='switch_links/2', events=[declared])
            assert proto.pop('link_failures') == schedule
            lf = r['link_failures']
            assert lf['configuration'] == schedule and lf['pending_events'] == 0 and lf['route_epoch'] == 2
            seen = Counter()
            revision = epoch = 0
            for event in lf['events']:
                kind = event['kind']
                seen[kind] += 1
                assert event['event_index'] == 0
                if kind in ('link_down', 'link_up'):
                    assert event['time_ns'] == declared['time_ns' if kind == 'link_down' else 'restore_time_ns']
                    assert event['route_epoch'] == epoch
                else:
                    v = event['notification_revision']
                    assert v > revision and kind == 'routes_updated'
                    revision, epoch = v, epoch+1
                    t = (4000 if v == 1 else declared['restore_time_ns'])+condition['delay_ns']
                    assert event['time_ns'] == t and event['route_epoch'] == epoch
                    assert event['known_state'] == ('down' if v == 1 else 'up')
                    assert event['excluded_directed_ports'] == (2 if v == 1 else 0)
                control_events.append(event['time_ns'])
            assert seen == Counter(link_down=1, link_up=1, routes_updated=2)
            assert revision == epoch == 2
            if condition['local']:
                assert {(x['time_ns'], x['node'], x['physical_state']) for x in lf['local_failover']['events']} == {
                    (t, node, state) for t, state in ((4000, 'down'), (declared['restore_time_ns'], 'up'))
                    for node in (128, 132)}
        assert proto == old_protocol
        cap = r['packet_capture']
        assert cap['complete'] and not cap['omitted_events'] and cap['observations'] == len(cap['events'])
        assert len(r['queue_events']) == r['queue_observations']
        codes = cap['event_codes']
        packets = [dict(zip(cap['columns'], row)) for row in cap['events']]
        topo = load(d/'topology.json')
        ports = {}
        for e in topo['links']:
            for node, port, peer, peer_port in ((e['src'], e['src_port'], e['dst'], e['dst_port']),
                                               (e['dst'], e['dst_port'], e['src'], e['src_port'])):
                ports[node, port] = peer, peer_port, e['rate_bps'], e['delay_ns']
        edge = f['failed_link']
        selected = {(128, edge['src_port']), (132, edge['dst_port'])}
        arrivals = defaultdict(list)
        identity = lambda x: (x['packet_uid'], x['message_id'], x['ip_protocol'], x['sequence'], x['model_bytes'])
        for x in packets:
            if x['event'] in (codes['rx'], codes['drop']):
                arrivals[(x['node'], x['port'], *identity(x))].append(x['time_ns'])
        actual_source, busy = Counter(), defaultdict(int)
        hops = same_ns = 0
        for x in packets:
            if x['event'] != codes['tx']:
                continue
            peer, port, rate, delay = ports[x['node'], x['port']]
            key = (peer, port, *identity(x))
            assert arrivals[key], ('unpaired physical transmission', name, x)
            at = arrivals[key].pop(0)
            assert abs(at-x['time_ns']-delay-x['model_bytes']*8*10**9/rate) <= 1.0
            start = x['time_ns']
            assert start >= busy[x['node'], x['port']], ('overlapping physical sends', x)
            busy[x['node'], x['port']] = start+round(x['model_bytes']*8*10**9/rate)
            hops += 1
            if condition and (x['node'], x['port']) in selected:
                if start in control_events:
                    same_ns += 1
                else:
                    assert not 4000 <= start < declared['restore_time_ns']
                restored_tx += start > declared['restore_time_ns']
            if x['node'] < 128 and x['ip_protocol'] == 17:
                actual_source[(start, x['message_id'], x['node'], x['sequence'],
                               x['data_payload_bytes'], x['packet_uid'])] += 1
        assert not any(arrivals.values()), 'arrival without physical send'
        if condition:
            assert restored_tx > 0
        n = r['service_transport']['native']
        high, expected_source = defaultdict(int), Counter()
        total = repeat = 0
        for row in n['source_packets']:
            x = dict(zip(n['source_packet_columns'], row))
            expected_source[(x['time_ns'], x['stream'], x['src'], x['sequence'], x['payload_bytes'], x['packet_uid'])] += 1
            start, end, sid = x['sequence'], x['sequence']+x['payload_bytes'], x['stream']
            assert start <= high[sid]
            repeat += min(end, high[sid])-start
            high[sid] = max(end, high[sid])
            total += end-start
        assert expected_source == actual_source
        messages = r['service_transport']['messages']
        logical = sum(m['bytes'] for m in messages)
        assert logical == sum(high.values()) == expected['logical_payload_bytes']
        assert total == expected['source_payload_bytes'] and repeat == total-logical == expected['retransmitted_payload_bytes']
        feedback = defaultdict(list)
        for x in packets:
            if x['event'] == codes['rx'] and x['node'] < 128 and x['ip_protocol'] == 252:
                feedback[x['node'], x['message_id'], x['time_ns']].append(x['sequence'])
        for m in messages:
            assert m['ready_ns'] <= m['published_ns'] <= m['receive_ready_ns'] <= m['ack_ns']
            assert any(seq >= m['end'] for seq in feedback[m['src'], m['stream'], m['ack_ns']]), ('missing real source ACK', m)
        assert n['recovery']['complete'] and not n['recovery']['failed']
        assert n['closed'] and n['physical_drained'] and n['inflight_packets'] == 0
        assert n['message_payload']['retained_bytes'] == r['service_transport']['message_buffers']['used_bytes'] == 0
        assert not r['service']['inflight_helpers']['attempts']
        for stream in n['streams']:
            assert stream['qp_creations'] <= 1
            assert stream['snd_una'] == stream['submitted_bytes'] == stream['published_bytes']
        history = load(d/'frontend.json')['history']
        groups = {str(i): max(h['finished_ns'] for h in history if h['group_index'] == i) for i in range(4)}
        assert groups == expected['group_complete_ns']
        for g in r['service']['phase_completion']['gates']:
            assert g['released_ns'] == max(m['arrived_ns'] for m in g['members'])
        assert r['finish_ns'] == expected['drain_ns']
        checks[name] = dict(messages=len(messages), physical_hops=hops, queue_rows=len(r['queue_events']),
            graph_files=len(graphs), restored_link_tx=restored_tx, boundary_tx_not_ordered=same_ns,
            actual_source_ACK_witnesses=len(messages))
        del r, packets
        gc.collect()
    normal = analysis['cases']['normal']['max_group_complete_ns']
    for window, effect in analysis['effects'].items():
        base, local, instant = (analysis['cases'][k+'-w'+window] for k in ('global', 'local', 'instant'))
        saved = base['max_group_complete_ns']-local['max_group_complete_ns']
        assert effect['max_group_saved_ns'] == saved
        assert effect['relative_global_percent'] == 100*saved/base['max_group_complete_ns']
        assert effect['fault_penalty_recovered_percent'] == 100*saved/(base['max_group_complete_ns']-normal)
        assert effect['group_saved_ns'] == {str(i): base['group_complete_ns'][str(i)]-local['group_complete_ns'][str(i)] for i in range(4)}
        assert effect['extra_source_payload_bytes'] == local['source_payload_bytes']-base['source_payload_bytes']
        assert effect['drain_saved_ns'] == base['drain_ns']-local['drain_ns']
        assert effect['local_minus_normal_ns'] == local['max_group_complete_ns']-normal
        assert effect['local_minus_instant_ns'] == local['max_group_complete_ns']-instant['max_group_complete_ns']
    proof = dict(passed=True, actual_Clos128_networks=7, all_original_graphs_equal=True,
        original_routes_and_declared_inputs_equal=True, all_physical_protocols_paired=True,
        all_message_source_ACK_witnesses_checked=True, source_bytes_and_final_buffers_checked=True,
        per_case=checks, analysis_sha256=sha(a.analysis), verifier_sha256=sha(Path(__file__)))
    with a.output.open('x') as s:
        json.dump(proof, s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, physical_hops=sum(v['physical_hops'] for v in checks.values()),
        messages=sum(v['messages'] for v in checks.values()))))


if __name__ == '__main__':
    main()
