"""Post-execution diagnosis of actual recovery timing and switch residence.

This reader was added after the two frozen conditions. It does not choose new
conditions or rerun networks. A sum of per-hop residence is not critical-path
latency or achievable gain. UID is lineage: repeated occurrences use FIFO joins.
"""
import argparse
from collections import Counter, defaultdict, deque
import csv
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def need(value, message):
    if not value:
        raise ValueError(message)


def diagnose(directory):
    result = load(directory / 'result.json')
    config = load(directory / 'input.json')
    native = result['service_transport']['native']
    streams = {s['id']: s for s in native['streams']}
    stream_group = {s['id']: gi for gi, group in enumerate(config['parallel']['groups'])
                    for s in native['streams'] if s['src'] in group}
    messages = result['service_transport']['messages']
    wave = load(directory / 'graphs/g0.w0/wave.json')
    ids = {m['id'] for m in wave['messages'] if m['layer'] == 0 and m['phase'] == 'dispatch'}
    last = max((m for m in messages if m['id'] in ids), key=lambda m: m['ack_ns'])
    gate = next(g for g in result['service']['phase_completion']['gates']
                if g['graph_id'] == 'g0.w0' and g['id'] == 'L0.dispatch')
    need(gate['released_ns'] == last['ack_ns'], 'first Dispatch not limited by last source completion')
    recovery = [dict(zip(native['recovery']['columns'], row)) for row in native['recovery']['events']]
    recovery = [r for r in recovery if r['message_id'] == last['stream'] and r['time_ns'] <= last['ack_ns']]
    source = [dict(zip(native['source_packet_columns'], row)) for row in native['source_packets']]
    source = [p for p in source if p['stream'] == last['stream'] and last['begin'] <= p['sequence'] < last['end']]
    pending, totals, count, maximum = defaultdict(deque), Counter(), Counter(), Counter()
    ports = defaultdict(lambda: dict(bytes=Counter(), residence=Counter(), packets=Counter()))
    endpoint_arrivals = endpoint_drops = 0
    critical_packets = []
    for values in result['packet_capture']['events']:
        p = dict(zip(result['packet_capture']['columns'], values))
        sid, node = p['message_id'], p['node']
        if p['ip_protocol'] != 17 or sid not in streams:
            continue
        group = stream_group[sid]
        if node < 128 and p['event'] in (1, 2) and node == streams[sid]['dst']:
            endpoint_arrivals += 1
            endpoint_drops += p['event'] == 2
        if sid == last['stream'] and last['begin'] <= p['sequence'] < last['end'] and node < 128:
            critical_packets.append(p)
        if node < 128:
            continue
        key = (node, p['packet_uid'], sid, p['sequence'], p['data_payload_bytes'], p['model_bytes'])
        if p['event'] == 1:
            pending[key].append(p['time_ns'])
        elif p['event'] == 0:
            need(bool(pending[key]), 'switch TX without matching RX occurrence')
            delay = p['time_ns'] - pending[key].popleft()
            need(delay >= 0, 'negative switch residence')
            totals[group] += delay
            count[group] += 1
            maximum[group] = max(maximum[group], delay)
            row = ports[node, p['port']]
            row['bytes'][group] += p['model_bytes']
            row['residence'][group] += delay
            row['packets'][group] += 1
    need(not any(pending.values()), 'unmatched switch RX at complete drain')
    shared = [dict(node=n, port=p,
        group0_data_model_bytes=v['bytes'][0], group1_data_model_bytes=v['bytes'][1],
        group0_residence_sum_ns=v['residence'][0], group1_residence_sum_ns=v['residence'][1])
        for (n, p), v in sorted(ports.items()) if v['bytes'][0] and v['bytes'][1]]
    need(bool(shared), 'no common data exit')
    result_summary = dict(last_first_dispatch_message=last, dispatch_gate_release_ns=gate['released_ns'],
        recovery_events_for_message_stream=recovery, actual_source_packets_for_message=source,
        critical_message_endpoint_packet_events=critical_packets,
        all_destination_data_arrivals_including_drops=endpoint_arrivals, all_destination_data_drops=endpoint_drops,
        whole_cluster_observed_destination_loss_fraction=endpoint_drops/endpoint_arrivals,
        switch_data_hops_checked=sum(count.values()),
        group_switch_residence_sum_ns=dict(totals), group_max_switch_residence_ns=dict(maximum),
        shared_directed_exits=shared,
        result_sha256=hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest(),
        scope='actual switch MacRx to MacTx residence; includes modeled queue/forwarding wait; cumulative across packets/hops, not business critical path')
    return result_summary


def main(root, output):
    output.mkdir(parents=True, exist_ok=False)
    cases = {case: diagnose(root / case) for case in ('normal', 'local')}
    fault = cases['local']
    source = fault['actual_source_packets_for_message']
    retries = [r for r in fault['recovery_events_for_message_stream'] if r['event'] == 2]
    need(len(retries) == 2 and len(source) == 6, 'unexpected critical first Dispatch recovery pattern')
    need(source[2]['time_ns'] == retries[0]['time_ns'] and source[4]['time_ns'] == retries[1]['time_ns'],
         'recovery request was not accompanied by actual source TX')
    need(0 < source[2]['time_ns'] < 100000 < source[4]['time_ns'], 'unexpected relation to fixed fault window')
    summary = dict(passed=True, cases=cases,
        critical_stream_id=fault['last_first_dispatch_message']['stream'],
        fault_end_ns=100000, first_retry_ns=source[2]['time_ns'], next_retry_ns=source[4]['time_ns'],
        interval_fault_end_to_next_critical_tx_ns=source[4]['time_ns']-100000,
        no_new_network_executed=True, analysis_registered_after_execution=True,
        limitations='RTO backoff is existing reference behavior. Gap after fault end is not proven eliminable headroom or an algorithm gain.')
    with (output / 'mechanism.json').open('x') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    for case, result in cases.items():
        rows = result['shared_directed_exits']
        with (output / (case + '-shared-exit-residence.csv')).open('x', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps({k: summary[k] for k in ('passed', 'critical_stream_id', 'first_retry_ns',
        'next_retry_ns', 'interval_fault_end_to_next_critical_tx_ns')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    main(args.root.resolve(), args.output.resolve())
