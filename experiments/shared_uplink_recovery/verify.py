"""Independently check raw bytes, physical DATA hops and held-constant inputs."""
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
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--delivery', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    f = load(a.study/'frozen.json')
    analysis = load(a.delivery/'analysis.json')
    assert sha(a.study/'frozen.json') == analysis['frozen_sha256']
    root = Path(__file__).resolve().parents[2]
    for group in ('source_sha256', 'compiled_sources_sha256'):
        for name, h in f[group].items():
            assert sha(root/name) == h
    normal_config = load(Path(f['normal'])/'input.json')
    normal_config['route_replay']['manifest']['path'] = '<manifest>'
    baseline_protocol = load(Path(f['normal'])/'rdma.json')
    total_hops = total_messages = total_queue_rows = 0
    counts = {}
    for name, expected in analysis['cases'].items():
        d = Path(f['normal']) if name == 'normal' else a.study/name
        r = load(d/'result.json')
        assert sha(d/'result.json') == expected['result_sha256']
        assert r['passed'] and r['drained'] and load(d/'verification.json')['passed']
        assert (r['gpu_count'], r['switch_count']) == (128, 20)
        conf = load(d/'input.json')
        conf['route_replay']['manifest']['path'] = '<manifest>'
        assert conf == normal_config
        assert len(conf['route_replay']['requests']) == 15
        assert conf['transport']['transport_recovery']['initial_rto_ns'] == 17000
        assert conf['inflight_helpers']['policy'] == 'observe_only/1'
        graphs = {str(x.relative_to(d/'graphs')): sha(x) for x in (d/'graphs').rglob('*') if x.is_file()}
        assert graphs == f['normal_graphs_sha256']
        protocol = load(d/'rdma.json')
        if name != 'normal':
            cond = f['conditions'][name]
            local = protocol.pop('local_failover', None)
            assert local == (dict(mode='live_ecmp/1') if cond['local_failover'] else None)
            fault = protocol.pop('link_failures')
            assert fault == dict(mode='switch_links/1', events=[dict(src=128, dst=132,
                time_ns=4000, route_update_delay_ns=cond['route_update_delay_ns'])])
            lf = r['link_failures']
            assert lf['pending_events'] == 0 and lf['route_epoch'] == 1
            assert [(x['kind'], x['time_ns']) for x in lf['events']] == [
                ('link_down', 4000), ('routes_updated', 4000+cond['route_update_delay_ns'])]
            assert lf == expected['link_failures']
        assert protocol == baseline_protocol
        cap = r['packet_capture']
        assert cap['complete'] and cap['omitted_events'] == 0
        assert cap['observations'] == len(cap['events'])
        assert len(r['queue_events']) == r['queue_observations']
        total_queue_rows += len(r['queue_events'])
        c = cap['columns']
        packets = [dict(zip(c, x)) for x in cap['events']]
        tx_code, rx_code, drop_code = (cap['event_codes'][k] for k in ('tx', 'rx', 'drop'))
        data = cap['protocol_codes']['data']
        topology = load(d/'topology.json')
        ports = {}
        for e in topology['links']:
            for src, sp, dst, dp in ((e['src'], e['src_port'], e['dst'], e['dst_port']),
                                     (e['dst'], e['dst_port'], e['src'], e['src_port'])):
                ports[src, sp] = dst, dp, e['rate_bps'], e['delay_ns']
        arrivals = defaultdict(list)
        for x in packets:
            if x['ip_protocol'] == data and x['event'] in (rx_code, drop_code):
                key = x['node'], x['port'], x['packet_uid'], x['message_id'], x['sequence'], x['model_bytes']
                arrivals[key].append(x['time_ns'])
        hops = 0
        source_actual = Counter()
        selected_groups = Counter()
        selected_bytes = Counter()
        n = r['service_transport']['native']
        streams = {s['id']: s for s in n['streams']}
        selected = {(128, f['failed_link']['src_port']), (132, f['failed_link']['dst_port'])}
        for x in packets:
            if x['event'] != tx_code or x['ip_protocol'] != data:
                continue
            peer, port, rate, delay = ports[x['node'], x['port']]
            key = peer, port, x['packet_uid'], x['message_id'], x['sequence'], x['model_bytes']
            times = arrivals[key]
            assert times, ('unpaired physical DATA transmission', name, x)
            arrival = times.pop(0)
            serialization = x['model_bytes']*8*10**9/rate
            assert abs(arrival-x['time_ns']-delay-serialization) <= 1.0, (name, x, arrival)
            hops += 1
            if x['node'] < 128:
                source_actual[x['time_ns'], x['message_id'], x['node'], x['sequence'],
                              x['data_payload_bytes'], x['packet_uid']] += 1
            if (x['node'], x['port']) in selected:
                if name != 'normal':
                    assert x['time_ns'] <= 4000
                group = streams[x['message_id']]['src'] % 4
                selected_groups[str(group)] += 1
                selected_bytes[str(group)] += x['data_payload_bytes']
        assert not any(arrivals.values()), 'DATA receive/drop without a physical send'
        assert dict(selected_groups) == {str(k): v for k, v in expected['selected_link_data_packets_by_group'].items()}
        assert dict(selected_bytes) == {str(k): v for k, v in expected['selected_link_payload_bytes_by_group'].items()}
        high = defaultdict(int)
        total = repeat = 0
        source_expected = Counter()
        for row in n['source_packets']:
            x = dict(zip(n['source_packet_columns'], row))
            source_expected[x['time_ns'], x['stream'], x['src'], x['sequence'], x['payload_bytes'], x['packet_uid']] += 1
            start, end, sid = x['sequence'], x['sequence']+x['payload_bytes'], x['stream']
            assert start <= high[sid]
            repeat += min(end, high[sid])-start
            high[sid] = max(high[sid], end)
            total += end-start
        assert source_expected == source_actual
        messages = r['service_transport']['messages']
        logical = sum(m['bytes'] for m in messages)
        assert logical == sum(high.values()) == expected['logical_payload_bytes']
        assert total == expected['source_payload_bytes'] and repeat == total-logical == expected['retransmitted_payload_bytes']
        assert n['recovery']['complete'] and not n['recovery']['failed']
        assert n['closed'] and n['physical_drained'] and n['inflight_packets'] == 0
        assert n['message_payload']['retained_bytes'] == r['service_transport']['message_buffers']['used_bytes'] == 0
        assert not r['service']['inflight_helpers']['attempts']
        for s in streams.values():
            assert s['qp_creations'] <= 1 and s['snd_una'] == s['submitted_bytes'] == s['published_bytes']
        for m in messages:
            assert m['ready_ns'] <= m['published_ns'] <= m['receive_ready_ns'] <= m['ack_ns']
        history = load(d/'frontend.json')['history']
        groups = {str(i): max(x['finished_ns'] for x in history if x['group_index'] == i) for i in range(4)}
        assert groups == expected['group_complete_ns']
        assert r['finish_ns'] == expected['drain_ns']
        total_hops += hops
        total_messages += len(messages)
        counts[name] = dict(physical_data_hops=hops, source_data_packets=len(n['source_packets']),
                            messages=len(messages), graph_files=len(graphs))
        del r, packets
        gc.collect()
    for delay, effect in analysis['effects'].items():
        base, local = analysis['cases']['global'+delay], analysis['cases']['local'+delay]
        assert effect['group_saved_ns'] == {str(i): base['group_complete_ns'][str(i)]-
                                          local['group_complete_ns'][str(i)] for i in range(4)}
        assert effect['extra_source_payload_bytes'] == local['source_payload_bytes']-base['source_payload_bytes']
    proof = dict(passed=True, new_networks=5, reused_normal=True, messages_checked=total_messages,
        physical_data_hops_checked=total_hops, queue_rows_checked=total_queue_rows,
        all_original_graphs_equal=True, inputs_only_declared_fault_changes=True,
        source_bytes_and_physical_DATA_arrivals_checked=True, final_buffers_zero=True,
        per_case=counts, analysis_sha256=sha(a.delivery/'analysis.json'))
    with a.output.open('x') as s:
        json.dump(proof, s, indent=2)
        s.write('\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
