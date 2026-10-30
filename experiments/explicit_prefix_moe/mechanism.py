"""Diagnose fixed ACK witness and actual gate predecessors; reconstruct RX curves."""
import argparse
from collections import defaultdict
import gc
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(Path(p).read_bytes())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def diagnose(d):
    r = load(d/'result.json')
    messages = r['service_transport']['messages']
    witness = next(m for m in messages if m['id'] == 3983)
    assert (witness['src'], witness['dst'], witness['stream']) == (1, 65, 65)
    cap = r['packet_capture']
    codes = cap['event_codes']
    packets = [dict(zip(cap['columns'], x)) for x in cap['events']]
    selected = [x for x in packets if x['message_id'] == witness['stream'] and x['time_ns'] <= witness['ack_ns']]
    n = r['service_transport']['native']
    recovery = [dict(zip(n['recovery']['columns'], x)) for x in n['recovery']['events']]
    source = [dict(zip(n['source_packet_columns'], x)) for x in n['source_packets']]
    final_ack = [x for x in selected if x['node'] == witness['src'] and x['ip_protocol'] == 252
        and x['event'] == codes['rx'] and x['time_ns'] == witness['ack_ns'] and x['sequence'] >= witness['end']]
    assert final_ack
    gate = next(g for g in r['service']['phase_completion']['gates'] if g['graph_id'] == 'g1.w0' and g['id'] == 'L0.dispatch')
    # Keep all tied members; a gate member is a graph node, not a message ID.
    last_members = [x for x in gate['members'] if x['arrived_ns'] == gate['released_ns']]
    waves = {p.parent.name: load(p) for p in (d/'graphs').glob('*/wave.json')}
    all_gates = []
    for g in r['service']['phase_completion']['gates']:
        ids = {m['id'] for m in waves[g['graph_id']]['messages'] if 'L'+str(m['layer'])+'.'+m['phase'] == g['id']}
        ms = [m for m in messages if m['id'] in ids]
        latest_ack = max(m['ack_ns'] for m in ms)
        all_gates.append(dict(graph=g['graph_id'], phase=g['id'], released_ns=g['released_ns'],
            tied_last_members=[x for x in g['members'] if x['arrived_ns'] == g['released_ns']],
            last_message_ACK_ns=latest_ack, last_message_ACKs=[m for m in ms if m['ack_ns'] == latest_ack]))
    ids = {m['id'] for m in waves['g1.w0']['messages'] if m['phase'] == 'dispatch' and m['layer'] == 0}
    phase_messages = [m for m in messages if m['id'] in ids]
    intervals = defaultdict(list)
    for m in phase_messages:
        intervals[m['stream']].append((m['begin'], m['end'], m['dst']))
    seen = defaultdict(list)
    received, confirmed = defaultdict(int), defaultdict(int)
    increments = []
    for x in packets:
        if x['event'] != codes['rx'] or x['ip_protocol'] != 17:
            continue
        sid = x['message_id']
        for begin, end, dst in intervals.get(sid, []):
            if x['node'] != dst:
                continue
            left, right = max(begin, x['sequence']), min(end, x['sequence']+x['data_payload_bytes'])
            if right <= left:
                continue
            new = right-left-sum(max(0, min(right, b)-max(left, a)) for a, b in seen[sid])
            assert new >= 0
            merged = []
            for a, b in sorted([*seen[sid], (left, right)]):
                if merged and a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                else:
                    merged.append((a, b))
            seen[sid] = merged
            if new:
                received[x['time_ns']] += new
                increments.append(dict(packet=x, new_payload_bytes=new))
    for m in phase_messages:
        confirmed[m['ack_ns']] += m['bytes']
    def cumulative(values):
        result, total = [[0, 0]], 0
        for t, value in sorted(values.items()):
            total += value
            result.append([t, total])
        return result
    rx_curve, ack_curve = cumulative(received), cumulative(confirmed)
    logical = sum(m['bytes'] for m in phase_messages)
    assert rx_curve[-1][1] == ack_curve[-1][1] == logical
    assert rx_curve[-1][0] == max(m['receive_ready_ns'] for m in phase_messages)
    assert ack_curve[-1][0] == gate['released_ns']
    history = load(d/'frontend.json')['history']
    group_finish = {str(i): max(h['finished_ns'] for h in history if h['group_index'] == i) for i in range(4)}
    last_time = max(group_finish.values())
    return dict(result_sha256=sha(d/'result.json'), message=witness, first_dispatch_gate=gate,
        tied_last_members=last_members, group_complete_ns=group_finish,
        final_groups=[g for g, t in group_finish.items() if t == last_time],
        source_packets=[x for x in source if x['stream'] == witness['stream'] and x['time_ns'] <= witness['ack_ns']],
        recovery_events=[x for x in recovery if x['message_id'] == witness['stream'] and x['time_ns'] <= witness['ack_ns']],
        drop_rows=[x for x in selected if x['event'] in (codes['drop'], codes.get('local_drop'))],
        final_ACK_path=[x for x in selected if x['ip_protocol'] == 252 and x['packet_uid'] == final_ack[0]['packet_uid']
            and x['event'] in (codes['tx'], codes['rx'], codes['drop'])],
        all_gate_predecessors=all_gates,
        collective=dict(graph='g1.w0', phase='L0.dispatch', payload_bytes=logical, messages=phase_messages,
            unique_received_curve_ns_bytes=rx_curve, fully_confirmed_message_curve_ns_bytes=ack_curve,
            actual_RX_increments=increments,
            scope='actual destination RX interval union; confirmation counts whole messages after real source ACK, not byte ACK'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('study', 'analysis', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    analysis = load(a.analysis)
    cases = {}
    for name in ('normal', 'local-w60', 'instant-w60', 'prefix-w20', 'prefix-w60'):
        cases[name] = diagnose(Path(analysis['cases'][name]['directory']))
        gc.collect()
    accounting = {}
    for name in ('prefix-w20', 'prefix-w60'):
        local = cases['local-w60'] if name.endswith('60') else None
        cur = cases[name]
        if local:
            gate_delta = local['first_dispatch_gate']['released_ns']-cur['first_dispatch_gate']['released_ns']
            later_delta = (local['group_complete_ns']['1']-local['first_dispatch_gate']['released_ns'])-(
                cur['group_complete_ns']['1']-cur['first_dispatch_gate']['released_ns'])
            assert gate_delta+later_delta == local['group_complete_ns']['1']-cur['group_complete_ns']['1']
            accounting[name] = dict(same_message_ACK_saved_ns=local['message']['ack_ns']-cur['message']['ack_ns'],
                first_dispatch_gate_saved_ns=gate_delta, later_group1_span_saved_ns=later_delta,
                group1_finish_saved_ns=gate_delta+later_delta,
                prefix_message_to_gate_slack_ns=cur['first_dispatch_gate']['released_ns']-cur['message']['ack_ns'])
    with a.output.open('x') as s:
        json.dump(dict(passed=True, analysis_sha256=sha(a.analysis), mechanism_sha256=sha(Path(__file__)),
            selection='message3983 fixed from preceding study; all gate ties retained; no input tuning',
            cases=cases, accounting=accounting), s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, accounting=accounting,
        first_dispatch={n: c['first_dispatch_gate']['released_ns'] for n, c in cases.items()})))


if __name__ == '__main__':
    main()
