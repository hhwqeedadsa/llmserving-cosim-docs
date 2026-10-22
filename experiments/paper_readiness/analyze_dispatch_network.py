"""Analyze only declared observed inputs and public packet/transport outputs."""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value): p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def analyze(observed, run, out):
    index = json.loads((observed / 'index.json').read_text()); cfg = index['config']
    summaries = []; rounds = []; port_rows = []; packet_rows = []
    for network in cfg['network_profiles']:
        for condition in cfg['placement_conditions']:
            case = network + '-' + condition
            path = run / case / 'result.json'; result = json.loads(path.read_text())
            assert result['passed'] and result['drained'] and result['native_network_executed']
            capture = result['packet_capture']; transport = result['persistent_transport']
            assert capture['complete'] and capture['omitted_events'] == 0
            assert transport['closed'] and transport['physical_drained'] and transport['inflight_packets'] == 0
            events = [dict(zip(capture['columns'], row)) for row in capture['events']]
            by_uid = defaultdict(list)
            for event in events: by_uid[event['packet_uid']].append(event)
            works = {w['id']: w for w in transport['work']}
            streams = {s['id']: s for s in transport['streams']}
            ports = {(p['node'], p['port']): p for p in result['ports']}
            source = {row[-1]: dict(zip(transport['source_packet_columns'], row)) for row in transport['source_packets']}
            assert len(source) == len(transport['source_packets'])
            packets = {}; work_packets = defaultdict(list); round_ports = defaultdict(Counter)
            for uid, rows in by_uid.items():
                tx = [e for e in rows if e['event'] == 0]; rx = [e for e in rows if e['event'] == 1]
                assert tx and len(tx) == len(rx) and tx[0]['node'] < 8 and rx[-1]['node'] < 8
                protocol = tx[0]['ip_protocol']; assert protocol in (17, 252)
                pair = (tx[0]['node'], rx[-1]['node'])
                sid = next(s['id'] for s in streams.values() if (s['src'], s['dst']) == (pair if protocol == 17 else pair[::-1]))
                seq = tx[0]['sequence']
                owner = next(w for w in works.values() if w['stream'] == sid and
                             (w['begin'] <= seq < w['end'] if protocol == 17 else w['begin'] < seq <= w['end']))
                step = owner['id'] // 16
                enqueue = {(e['node'], e['port']): e['time_ns'] for e in rows if e['event'] == 3}
                dequeue = {(e['node'], e['port']): e['time_ns'] for e in rows if e['event'] == 4}
                assert enqueue.keys() == dequeue.keys()
                waits = [dict(node=n, port=p, peer=ports[n, p]['peer'], tier=ports[n, p]['tier'],
                              wait_ns=dequeue[n, p] - enqueue[n, p]) for n, p in enqueue]
                serialization = sum(r['time_ns'] - t['time_ns'] - ports[t['node'], t['port']]['delay_ns'] for t, r in zip(tx, rx))
                propagation = sum(ports[t['node'], t['port']]['delay_ns'] for t in tx)
                wait = sum(w['wait_ns'] for w in waits)
                assert rx[-1]['time_ns'] - tx[0]['time_ns'] == serialization + propagation + wait
                record = dict(case=case, uid=uid, work=owner['id'], step=step, protocol=protocol, sequence=seq,
                    src=pair[0], dst=pair[1], model_bytes=tx[0]['model_bytes'], payload_bytes=tx[0]['data_payload_bytes'],
                    first_tx_ns=tx[0]['time_ns'], last_rx_ns=rx[-1]['time_ns'], hops=len(tx),
                    serialization_ns=serialization, propagation_ns=propagation, switch_wait_ns=wait,
                    path='>'.join(map(str, [t['node'] for t in tx] + [rx[-1]['node']])))
                packets[uid] = dict(**record, waits=waits)
                packet_rows.append(record)
                work_packets[owner['id']].append(uid)
                for t in tx: round_ports[step][t['node'], t['port']] += t['model_bytes']
            for step in range(4):
                group = [w for w in works.values() if w['id'] // 16 == step]
                ready = group[0]['ready_ns']; assert all(w['ready_ns'] == ready for w in group)
                latest = max(w['acked_ns'] for w in group)
                critical = []
                for work in group:
                    if work['acked_ns'] != latest: continue
                    data = [packets[u] for u in work_packets[work['id']] if packets[u]['protocol'] == 17]
                    tail = max(data, key=lambda row: row['last_rx_ns'])
                    ack = min((packets[u] for u in work_packets[work['id']] if packets[u]['protocol'] == 252 and
                               packets[u]['sequence'] >= work['end']), key=lambda row: row['last_rx_ns'])
                    assert tail['last_rx_ns'] == work['accepted_ns'] and ack['last_rx_ns'] == work['acked_ns']
                    components = dict(source_tail_offset_ns=tail['first_tx_ns'] - ready,
                        data_serialization_ns=tail['serialization_ns'], data_propagation_ns=tail['propagation_ns'],
                        data_switch_wait_ns=tail['switch_wait_ns'], ack_generation_gap_ns=ack['first_tx_ns'] - tail['last_rx_ns'],
                        ack_serialization_ns=ack['serialization_ns'], ack_propagation_ns=ack['propagation_ns'],
                        ack_switch_wait_ns=ack['switch_wait_ns'])
                    assert sum(components.values()) == latest - ready
                    critical.append(dict(work=work['id'], data_uid=tail['uid'], ack_uid=ack['uid'],
                        components=components, data_path=tail['path'], ack_path=ack['path'],
                        data_waits=tail['waits'], ack_waits=ack['waits']))
                load = round_ports[step]
                busiest = max(load, key=lambda key: load[key] / ports[key]['rate_bps'])
                rounds.append(dict(case=case, step=step, work_count=len(group), ready_ns=ready,
                    accepted_ns=max(w['accepted_ns'] for w in group), acked_ns=latest, duration_ns=latest-ready,
                    critical=critical, busiest_port=dict(node=busiest[0], port=busiest[1], peer=ports[busiest]['peer'],
                        tier=ports[busiest]['tier'], model_bytes=load[busiest],
                        serialization_lower_bound_ns=load[busiest]*8e9/ports[busiest]['rate_bps'])))
            for port in result['ports']:
                port_rows.append(dict(case=case, **{k:port[k] for k in ('node', 'port', 'peer', 'tier', 'rate_bps',
                    'model_tx_bytes', 'data_tx_bytes', 'control_tx_bytes', 'data_packets')},
                    queue_area_byte_ns=port.get('queue_metrics', {}).get('area_byte_ns', 0),
                    queue_peak_bytes=port.get('queue_metrics', {}).get('peak_bytes', 0)))
            queue_ports = [p for p in result['ports'] if 'queue_metrics' in p]
            busiest = max(result['ports'], key=lambda p: p['model_tx_bytes']/p['rate_bps'])
            summaries.append(dict(case=case, network=network, condition=condition, work_count=len(works),
                data_payload_bytes=sum(row['payload_bytes'] for row in source.values()),
                data_source_packets=len(source), accepted_ns=max(w['accepted_ns'] for w in works.values()),
                acked_ns=max(w['acked_ns'] for w in works.values()), finish_ns=result['finish_ns'],
                total_queue_area_byte_ns=sum(p['queue_metrics']['area_byte_ns'] for p in queue_ports),
                max_queue_bytes=max(p['queue_metrics']['peak_bytes'] for p in queue_ports),
                ecn_ce_seen=sum(p['ecn_ce_seen'] for p in result['ports']),
                pfc_pause=sum(p['pfc_pause'] for p in result['ports']), fault_drops=result['fault_drops'],
                busiest_port={k:busiest[k] for k in ('node', 'port', 'peer', 'tier', 'rate_bps', 'model_tx_bytes')},
                result_sha256=sha(path)))
    out.mkdir()
    for name, rows in (('ports.csv', port_rows), ('packets.csv', packet_rows)):
        with (out / name).open('w', newline='') as f:
            writer=csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    save(out / 'rounds.json', rounds)
    save(out / 'summary.json', dict(schema_version='moe-dispatch-network-analysis/1', cases=summaries,
        observed_index_sha256=sha(observed/'index.json'), packets_csv_sha256=sha(out/'packets.csv'),
        ports_csv_sha256=sha(out/'ports.csv'), rounds_sha256=sha(out/'rounds.json'),
        analysis_reads_truth=False, full_inference_modeled=False, latency_hardware_calibrated=False))
    print(json.dumps(dict(cases=len(summaries), rounds=len(rounds), packets=len(packet_rows))))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('observed', 'run', 'output'): p.add_argument('--'+name, type=Path, required=True)
    a=p.parse_args(); analyze(a.observed.resolve(), a.run.resolve(), a.output.resolve())


if __name__ == '__main__': main()
