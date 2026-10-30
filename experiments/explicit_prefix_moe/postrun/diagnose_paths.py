"""Post-result diagnosis: ACK timer restart and selected last-predecessor paths.

Not preregistered execution code; reads retained results and never runs a network.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_case(d):
    r = load(d/'result.json')
    cap = r['packet_capture']
    return r, [dict(zip(cap['columns'], row)) for row in cap['events']]


def packet_path(events, received):
    path = [x for x in events if x['packet_uid'] == received['packet_uid'] and
        x['message_id'] == received['message_id'] and x['ip_protocol'] == received['ip_protocol'] and
        x['sequence'] == received['sequence'] and x['event'] in (0, 1) and x['time_ns'] <= received['time_ns']]
    assert len(path) % 2 == 0 and path[-1] == received
    hops, queues = [], []
    for tx, rx in zip(path[::2], path[1::2]):
        assert tx['event'] == 0 and rx['event'] == 1
        assert tx['model_bytes'] == rx['model_bytes']
        hops.append(dict(tx=tx, rx=rx, elapsed_ns=rx['time_ns']-tx['time_ns']))
    for before, after in zip(hops, hops[1:]):
        rx, tx = before['rx'], after['tx']
        assert rx['node'] == tx['node'] and tx['time_ns'] >= rx['time_ns']
        queues.append(dict(node=tx['node'], port=tx['port'], arrived_ns=rx['time_ns'],
            sent_ns=tx['time_ns'], residence_ns=tx['time_ns']-rx['time_ns']))
    elapsed = received['time_ns']-path[0]['time_ns']
    assert sum(h['elapsed_ns'] for h in hops)+sum(q['residence_ns'] for q in queues) == elapsed
    return dict(hops=hops, switch_residence=queues, elapsed_ns=elapsed,
        switch_residence_ns=sum(q['residence_ns'] for q in queues), first_tx_ns=path[0]['time_ns'])


def message(r, events, mid):
    m = next(m for m in r['service_transport']['messages'] if m['id'] == mid)
    data = [x for x in events if x['event'] == 1 and x['node'] == m['dst'] and x['ip_protocol'] == 17
        and x['message_id'] == m['stream'] and x['time_ns'] == m['receive_ready_ns']]
    ack = [x for x in events if x['event'] == 1 and x['node'] == m['src'] and x['ip_protocol'] == 252
        and x['message_id'] == m['stream'] and x['time_ns'] == m['ack_ns'] and x['sequence'] >= m['end']]
    assert len(data) == len(ack) == 1
    dp, ap = packet_path(events, data[0]), packet_path(events, ack[0])
    components = dict(ready_to_publish_ns=m['published_ns']-m['ready_ns'],
        publish_to_last_DATA_send_ns=dp['first_tx_ns']-m['published_ns'],
        last_DATA_physical_hops_ns=sum(h['elapsed_ns'] for h in dp['hops']),
        last_DATA_switch_residence_ns=dp['switch_residence_ns'],
        receive_ready_to_final_ACK_send_ns=ap['first_tx_ns']-m['receive_ready_ns'],
        final_ACK_physical_hops_ns=sum(h['elapsed_ns'] for h in ap['hops']),
        final_ACK_switch_residence_ns=ap['switch_residence_ns'])
    assert sum(components.values()) == m['ack_ns']-m['ready_ns']
    # Audit actual serial occupants of every positive last-DATA switch wait.
    competitors = []
    for q in dp['switch_residence']:
        if not q['residence_ns']:
            continue
        occupants = Counter()
        for x in events:
            if x['event'] == 0 and (x['node'], x['port']) == (q['node'], q['port']):
                port = next(p for p in r['ports'] if (p['node'], p['port']) == (x['node'], x['port']))
                duration = round(x['model_bytes']*8*10**9/port['rate_bps'])
                overlap = max(0, min(q['sent_ns'], x['time_ns']+duration)-max(q['arrived_ns'], x['time_ns']))
                if overlap:
                    occupants[str(x['ip_protocol'])] += overlap
        assert sum(occupants.values()) <= q['residence_ns']
        competitors.append(dict(**q, actual_serial_occupancy_ns_by_protocol=dict(occupants),
            uncovered_ns=q['residence_ns']-sum(occupants.values())))
    return dict(message=m, components_ns=components, last_DATA_path=dp, final_ACK_path=ap,
                last_DATA_queue_occupancy=competitors)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    analysis = load(a.stage/'analysis.json')
    cases = {}
    for name, ids in [('local-w20', (4000, 23406)), ('prefix-w20', (4000, 23406)),
                      ('instant-w60', (4000, 23531)), ('prefix-w60', (4000, 23531))]:
        d = Path(analysis['cases'][name]['directory'])
        r, events = read_case(d)
        n = r['service_transport']['native']
        source = [dict(zip(n['source_packet_columns'], row)) for row in n['source_packets']]
        rec = [dict(zip(n['recovery']['columns'], row)) for row in n['recovery']['events']]
        codes = n['recovery']['event_codes']
        msg = next(m for m in r['service_transport']['messages'] if m['id'] == 4000)
        sid = msg['stream']
        progress = [x for x in rec if x['message_id'] == sid and x['event'] == codes['ack_progress'] and x['time_ns'] < msg['ack_ns']]
        retries = [x for x in rec if x['message_id'] == sid and x['event'] == codes['timeout_retransmit'] and x['time_ns'] < msg['ack_ns']]
        if name.startswith('prefix') or name.startswith('instant'):
            assert len(progress) == len(retries) == 1
            assert progress[0]['acknowledged_bytes'] == 9000
            assert progress[0]['deadline_ns'] == progress[0]['time_ns']+17000 == retries[0]['time_ns']
        else:
            assert retries[0]['time_ns'] == 1030+17000
        # Pin physical DATA loss and actual ACKs instead of inferring loss from retry.
        initial_loss = [x for x in events if x['message_id'] == sid and x['ip_protocol'] == 17
            and x['packet_uid'] == 228 and x['event'] in (2, 5)]
        assert initial_loss
        progress_ack_paths = []
        for row in progress:
            matches = [x for x in events if x['event'] == 1 and x['node'] == msg['src'] and x['ip_protocol'] == 252
                and x['message_id'] == sid and x['sequence'] == 9000 and x['time_ns'] == row['time_ns']]
            assert len(matches) == 1
            progress_ack_paths.append(packet_path(events, matches[0]))
        first = next(g for g in r['service']['phase_completion']['gates'] if g['graph_id'] == 'g1.w0' and g['id'] == 'L0.dispatch')
        final = next(g for g in r['service']['phase_completion']['gates'] if g['graph_id'] == 'g1.w1' and g['id'] == 'L1.combine')
        cases[name] = dict(result_sha256=sha(d/'result.json'), messages={str(i): message(r, events, i) for i in ids},
            timer=dict(progress=progress, retries=retries, initial_lost_tail_packet=initial_loss,
                progress_ACK_paths=progress_ack_paths,
                source_packets=[x for x in source if x['stream'] == sid and x['time_ns'] <= msg['ack_ns']]),
            first_dispatch_gate_ns=first['released_ns'], final_combine_gate_ns=final['released_ns'])
    short_base, short = cases['local-w20'], cases['prefix-w20']
    base_m, new_m = (c['messages']['23406'] for c in (short_base, short))
    delta = {k: new_m['components_ns'][k]-base_m['components_ns'][k] for k in new_m['components_ns']}
    ready_delta = new_m['message']['ready_ns']-base_m['message']['ready_ns']
    assert ready_delta+sum(delta.values()) == short['final_combine_gate_ns']-short_base['final_combine_gate_ns']
    b, c = cases['instant-w60'], cases['prefix-w60']
    bm, cm = b['messages']['23531'], c['messages']['23531']
    long_delta = {k: cm['components_ns'][k]-bm['components_ns'][k] for k in cm['components_ns']}
    baseline_slack = b['final_combine_gate_ns']-bm['message']['ack_ns']
    assert cm['message']['ready_ns']-bm['message']['ready_ns']+sum(long_delta.values())-baseline_slack == c['final_combine_gate_ns']-b['final_combine_gate_ns']
    data = dict(passed=True, post_result_diagnosis=True, no_new_networks=True, cases=cases,
        short_final_same_message=dict(message_id=23406, ready_delta_ns=ready_delta, components_delta_ns=delta,
            group1_finish_later_ns=short['final_combine_gate_ns']-short_base['final_combine_gate_ns']),
        long_final_same_message=dict(message_id=23531, ready_delta_ns=cm['message']['ready_ns']-bm['message']['ready_ns'],
            components_delta_ns=long_delta, instant_gate_slack_ns=baseline_slack,
            group1_finish_later_ns=c['final_combine_gate_ns']-b['final_combine_gate_ns']),
        source_sha256=sha(Path(__file__)), timer_sources_sha256={p: sha(Path(__file__).resolve().parents[3]/p)
            for p in ('backend/retransmission_timer.hh', 'backend/rdma/persistent_streams.cc')})
    with (a.stage/'path_diagnosis.json').open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(json.dumps(dict(passed=True, short=data['short_final_same_message'], long=data['long_final_same_message'])))


if __name__ == '__main__':
    main()
