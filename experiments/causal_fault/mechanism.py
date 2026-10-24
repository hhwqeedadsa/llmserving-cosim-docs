"""Trace loss and feedback for every first-layer Dispatch, without truth input."""
import argparse
from collections import Counter
import json
from pathlib import Path


def load(p): return json.loads(p.read_text())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for k in ('network', 'analysis', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); analysis = load(a.analysis); results = {}
    for name, case in analysis['cases'].items():
        r = load(a.network/name/'result.json'); t = r['persistent_transport']; c = r['packet_capture']
        events = [dict(zip(c['columns'], e)) for e in c['events']]
        source = [dict(zip(t['source_packet_columns'], e)) for e in t['source_packets']]
        streams = {v['id']: v for v in t['streams']}; work = {v['id']: v for v in t['work']}
        recovery = [dict(zip(t['recovery']['columns'], e)) for e in t['recovery']['events']]
        frontier = {v['work'] for v in case['windows'][-1]['frontier']['writes']}
        windows = []
        for declared in case['writes']:
            if declared['graph_id'] != 'g0.w0' or not declared['name'].startswith('L0.dispatch.'): continue
            w = work[declared['work']]; sid = w['stream']; s = streams[sid]
            packets = [v for v in source if v['stream'] == sid and w['begin'] <= v['sequence'] < v['sequence']+v['payload_bytes'] <= w['end']]
            first = {}
            for v in packets: first.setdefault(v['sequence'], v)
            drops = {v['packet_uid'] for v in events if v['event'] in (2, 5) and v['ip_protocol'] == 17 and v['message_id'] == sid}
            feedback = [v for v in events if v['event'] == 1 and v['ip_protocol'] in (252, 253) and v['message_id'] == sid and v['node'] == s['src']]
            timeouts = [v for v in recovery if v['message_id'] == sid and v['event'] == 2]
            first_feedback = min(v['time_ns'] for v in feedback)
            initial_lost = [v for v in first.values() if v['packet_uid'] in drops]
            row = dict(work=w['id'], stream=sid, bytes=w['bytes'], initial_packets=len(first),
                initial_packets_lost=len(initial_lost), initial_payload_lost=sum(v['payload_bytes'] for v in initial_lost),
                first_source_tx_ns=min(v['time_ns'] for v in packets), first_feedback_rx_ns=first_feedback,
                first_timeout=timeouts[0] if timeouts else None,
                timeout_precedes_any_feedback=bool(timeouts and timeouts[0]['time_ns'] < first_feedback),
                accepted_ns=w['accepted_ns'], acked_ns=w['acked_ns'], final_declared_frontier=w['id'] in frontier,
                receive_witness=declared['witness'])
            if timeouts:
                # Verify the advertised timer matches the actual prior armed deadline.
                timers = [v for v in recovery if v['message_id'] == sid and v['event'] in (0, 1) and v['time_ns'] < timeouts[0]['time_ns']]
                timer = timers[-1]; assert timer['deadline_ns'] == timeouts[0]['time_ns']
                row['prior_timer'] = timer
            windows.append(row)
        results[name] = windows
    a.output.write_text(json.dumps(dict(cases=results, checked_all_first_layer_dispatch=True,
        scope='observed whole initial loss and actual timer/feedback ordering; not earlier failure observability or recoverable application gain'), ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__': main()
