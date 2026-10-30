"""Explain unchanged business completion despite real DATA/ACK interference."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

def load(path):
    return json.loads(path.read_bytes())

def ack_path(result, message):
    # Select the actual first cumulative ACK covering the complete message.
    events = result['packet_capture']['events']
    received = [e for e in events if e[1] == message['src'] and e[3] == 1
                and e[5] == message['stream'] and e[6] == 252 and e[7] >= message['end']]
    final = min(received, key=lambda e: e[0])
    assert final[0] == message['ack_ns']
    packet = [e for e in events if e[4] == final[4]]
    source = [e for e in packet if e[1] == message['dst'] and e[3] == 0]
    assert len(source) == 1
    pending, hops = {}, []
    for event in packet:
        if event[1] < 128:
            continue
        if event[3] == 1:
            assert event[1] not in pending
            pending[event[1]] = event[0]
        elif event[3] == 0:
            hops.append(dict(node=event[1], port=event[2], rx_ns=pending.pop(event[1]), tx_ns=event[0]))
    assert not pending and hops
    return dict(sequence=final[7], source_tx_ns=source[0][0], source_rx_ns=final[0],
                hops=hops, residence_ns=sum(e['tx_ns']-e['rx_ns'] for e in hops))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.frozen)
    analysis = load(args.analysis/'analysis.json')
    paths = [Path(frozen['inherited_cases']['control-local']['output']),
             Path(frozen['new_cases']['local']['output'])]
    baseline, changed = [load(path/'result.json') for path in paths]
    config = load(paths[0]/'input.json')
    healthy = set(config['parallel']['groups'][2])
    old = {m['id']: m for m in baseline['service_transport']['messages'] if m['src'] in healthy}
    new = {m['id']: m for m in changed['service_transport']['messages'] if m['src'] in healthy}
    old_gates = {(g['graph_id'], g['id']): g for g in baseline['service']['phase_completion']['gates']}
    new_gates = {(g['graph_id'], g['id']): g for g in changed['service']['phase_completion']['gates']}
    assert set(old) == set(new)
    messages = []
    for identity, message in new.items():
        original = old[identity]
        if message == original:
            continue
        graph_names = []
        for graph in ('g2.w0', 'g2.w1'):
            wave = load(paths[1]/'graphs'/graph/'wave.json')
            if any(m['id'] == identity for m in wave['messages']):
                graph_names.append(graph)
        assert len(graph_names) == 1
        key = graph_names[0], f'L{message["layer"]}.{message["phase"]}'
        # Individual ranks can arrive later without moving the maximum.
        # Preserve their actual arrivals; compare the phase's release time.
        assert old_gates[key]['released_ns'] == new_gates[key]['released_ns']
        before, after = ack_path(baseline, original), ack_path(changed, message)
        assert before['sequence'] == after['sequence']
        assert [(h['node'], h['port']) for h in before['hops']] == [(h['node'], h['port']) for h in after['hops']]
        delta = message['ack_ns']-original['ack_ns']
        residence_delta = after['residence_ns']-before['residence_ns']
        source_delta = after['source_tx_ns']-before['source_tx_ns']
        assert source_delta+residence_delta == delta
        assert message['receive_ready_ns'] == original['receive_ready_ns']
        assert message['ready_ns'] == original['ready_ns']
        slack = old_gates[key]['released_ns']-original['ack_ns']
        assert slack > delta
        messages.append(dict(message=identity, graph=key[0], phase=key[1],
            src=message['src'], dst=message['dst'], ready_change_ns=0, receive_change_ns=0,
            ack_change_ns=delta, ack_source_tx_change_ns=source_delta,
            ack_path_residence_change_ns=residence_delta,
            baseline_phase_slack_ns=slack, remaining_phase_slack_ns=slack-delta,
            phase_release_change_ns=0, baseline_ack=before, calibrated_ack=after))
    with (args.analysis/'calibrated-local-healthy-matched.csv').open() as stream:
        hops = [row for row in csv.DictReader(stream) if int(row['residence_change_ns']) != 0]
    gates = [dict(graph=k[0], phase=k[1], change_ns=new_gates[k]['released_ns']-v['released_ns'])
             for k, v in old_gates.items() if k[0].startswith('g0.')]
    calls = analysis['cases']['calibrated-local']['calls']['target']
    replica = calls['attempts']['replica']
    primary = calls['attempts']['primary']
    remaining = primary['finished_ns']-replica['submitted_ns']
    copy_time = replica['finished_ns']-replica['submitted_ns']
    assert copy_time > remaining and calls['winner'] == 'primary'
    out = dict(passed=True, new_network_executions=0,
        changed_healthy_messages=messages, changed_healthy_data_hops=hops,
        healthy_data_hop_residence_sum_change_ns=sum(int(r['residence_change_ns']) for r in hops),
        target_primary_phase_changes=gates,
        actual_race=dict(backup_start_ns=replica['submitted_ns'],
            original_remaining_until_result_ns=remaining, backup_execution_ns=copy_time,
            backup_finished_ns=replica['finished_ns'], primary_finished_ns=primary['finished_ns'],
            losing_backup_after_result_ns=replica['finished_ns']-primary['finished_ns'],
            primary_waves=primary['waves'], replica_waves=replica['waves']),
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='Read-only retrospective evaluation. Remaining runtime and ACK packet paths are truth for explanation, never online policy inputs. No lower-bound or optimality claim.')
    with args.output.open('x') as stream:
        json.dump(out, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(passed=True, changed_healthy_messages=len(messages),
        changed_healthy_data_hops=len(hops), race=out['actual_race'])))

if __name__ == '__main__':
    main()
