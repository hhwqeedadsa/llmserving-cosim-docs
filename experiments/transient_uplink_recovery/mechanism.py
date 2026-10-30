"""Post-run diagnosis of an actual first-Dispatch ACK bottleneck, no network replay."""
import argparse
from collections import defaultdict, deque
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    out = dict(selection='post-run local-w60 last group1 first-Dispatch predecessor; not a preselected victim')
    for name in ('local-w60', 'instant-w60'):
        d = a.study/name
        r = load(d/'result.json')
        m = next(m for m in r['service_transport']['messages'] if m['id'] == 3983)
        assert (m['src'], m['dst'], m['stream']) == (1, 65, 65)
        gate = next(g for g in r['service']['phase_completion']['gates']
                    if g['graph_id'] == 'g1.w0' and g['id'] == 'L0.dispatch')
        packets = [dict(zip(r['packet_capture']['columns'], row)) for row in r['packet_capture']['events']]
        packets = [x for x in packets if x['message_id'] == m['stream'] and x['time_ns'] <= m['ack_ns']]
        codes = r['packet_capture']['event_codes']
        native = r['service_transport']['native']
        source = [dict(zip(native['source_packet_columns'], row)) for row in native['source_packets']]
        source = [x for x in source if x['stream'] == m['stream'] and x['time_ns'] <= m['ack_ns']]
        events = [dict(zip(native['recovery']['columns'], row)) for row in native['recovery']['events']]
        events = [x for x in events if x['message_id'] == m['stream'] and x['time_ns'] <= m['ack_ns']]
        losses = [x for x in packets if x['event'] in (codes['drop'], codes['local_drop'])]
        received = [x for x in packets if x['node'] == m['src'] and x['ip_protocol'] == 252 and x['event'] == codes['rx']]
        final = [x for x in received if x['time_ns'] == m['ack_ns'] and x['sequence'] >= m['end']]
        assert len(final) == 1
        ack_path = [x for x in packets if x['packet_uid'] == final[0]['packet_uid'] and
                    x['ip_protocol'] == 252 and x['event'] in (codes['tx'], codes['rx'], codes['drop'])]
        assert ack_path[0]['event'] == codes['tx'] and ack_path[-1] == final[0]
        data_ready = [x for x in packets if x['time_ns'] == m['receive_ready_ns'] and x['node'] == m['dst']
                      and x['ip_protocol'] == 17 and x['event'] == codes['rx']]
        assert data_ready
        history = load(d/'frontend.json')['history']
        finish = max(h['finished_ns'] for h in history if h['group_index'] == 1)
        out[name] = dict(result_sha256=sha(d/'result.json'), message=m, first_dispatch_gate=gate,
            source_packets=source, recovery_events=events, drop_rows=losses,
            actual_source_ACKs=received, actual_data_ready_RX=data_ready, final_ACK_path=ack_path,
            group1_finished_ns=finish)
    local, instant = out['local-w60'], out['instant-w60']
    m = local['message']
    assert m['ack_ns'] == local['first_dispatch_gate']['released_ns']
    assert [(x['rank'], x['arrived_ns']) for x in local['first_dispatch_gate']['members']
            if x['arrived_ns'] == m['ack_ns']] == [(1, m['ack_ns'])]
    topology = load(a.study/'local-w60/topology.json')
    adj = defaultdict(list)
    for edge in topology['links']:
        adj[edge['src']].append((edge['dst'], edge['src_port']))
        adj[edge['dst']].append((edge['src'], edge['dst_port']))
    distance, queue = {m['src']: 0}, deque([m['src']])
    while queue:
        node = queue.popleft()
        for peer, _ in adj[node]:
            if peer not in distance:
                distance[peer] = distance[node]+1
                if peer >= 128:
                    queue.append(peer)
    choices = [(peer, port) for peer, port in adj[132] if distance[peer] == distance[132]-1]
    assert choices == [(128, 1)]
    ack_losses = [x for x in local['drop_rows'] if x['ip_protocol'] == 252]
    assert len(ack_losses) == 4 and all(x['node'] == 132 and x['event'] == 5 for x in ack_losses)
    timeout_code = load(a.study/'local-w60/result.json')['service_transport']['native']['recovery']['event_codes']['timeout_retransmit']
    retries = [x['time_ns'] for x in local['recovery_events'] if x['event'] == timeout_code]
    assert retries == [18754, 52754, 120754]
    assert not any(64000 < x['time_ns'] < 120754 for x in local['source_packets'])
    assert local['actual_source_ACKs'][0]['time_ns'] == m['ack_ns'] == 133846
    assert m['receive_ready_ns'] == 26170
    slack = instant['first_dispatch_gate']['released_ns']-instant['message']['ack_ns']
    gate_delta = local['first_dispatch_gate']['released_ns']-instant['first_dispatch_gate']['released_ns']
    message_delta = m['ack_ns']-instant['message']['ack_ns']
    assert message_delta-slack == gate_delta == 98281
    after_delta = (local['group1_finished_ns']-local['first_dispatch_gate']['released_ns'])-(
        instant['group1_finished_ns']-instant['first_dispatch_gate']['released_ns'])
    assert gate_delta+after_delta == local['group1_finished_ns']-instant['group1_finished_ns'] == 98113
    out['accounting'] = dict(original_ACK_next_hops_at_Agg132=choices,
        local_filter_leaves_no_original_candidate=True, ACKs_dropped_at_Agg132_ns=[x['time_ns'] for x in ack_losses],
        physical_restore_ns=64000, first_complete_data_ns=26170, next_retry_ns=120754, source_ACK_ns=133846,
        restore_to_retry_ns=120754-64000, retry_to_ACK_ns=133846-120754, restore_to_ACK_ns=133846-64000,
        last_failed_ACK_before_restore_ns=64000-max(x['time_ns'] for x in ack_losses),
        same_message_ACK_delta_ns=message_delta, instant_gate_slack_ns=slack,
        first_dispatch_gate_delta_ns=gate_delta, later_group_span_delta_ns=after_delta,
        group1_finish_delta_ns=98113,
        scope='Source confirmation blocks original SEND/Dispatch gate; no new ACK path, retry-on-link-up or performance counterfactual implemented')
    out['passed'] = True
    with a.output.open('x') as s:
        json.dump(out, s, indent=2); s.write('\n')
    print(json.dumps(out['accounting']))


if __name__ == '__main__':
    main()
