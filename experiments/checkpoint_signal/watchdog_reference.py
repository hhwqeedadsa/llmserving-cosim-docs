"""Evaluate existing source-confirmation timeout predicates, never inject them."""
import argparse
import hashlib
import json
from pathlib import Path

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.prepared/'frozen.json')
    root = Path(__file__).resolve().parents[2]
    code = root/'backend/rdma/source_ack_watchdog.hh'
    documentation = root/'docs/source_ack_watchdog.md'
    assert 'now+option_.timeout' in code.read_text() and 'ns3::Simulator::Schedule(ns3::NanoSeconds(option_.timeout)' in code.read_text()
    assert '"timeout_ns": 20000' in documentation.read_text()
    data = {}
    for condition, record in frozen['records'].items():
        path = Path(record['output'])
        wave = load(path/'graphs/g0.w0/wave.json')
        ids = {m['id'] for m in wave['messages'] if m['layer'] == 0 and m['phase'] == 'dispatch'}
        callbacks = [json.loads(line) for line in (path/'execution_events.jsonl').read_text().splitlines()]
        times = {(e['message'], e['kind']): e['time_ns'] for e in callbacks if e.get('graph_id') == 'g0.w0'
                 and e['kind'].startswith('message_') and e['message'] in ids}
        data[condition] = {identity: dict(post_ns=times[identity, 'message_send_post'], ack_ns=times[identity, 'message_send_complete'])
                           for identity in ids}
    # Freeze the fitted reference solely from the normal trace, before using
    # the local ACK times for evaluation.
    normal_max = max(v['ack_ns']-v['post_ns'] for v in data['normal'].values())
    fitted = (normal_max//1000+1)*1000
    plans = dict(documented_example=20000, normal_calibrated=fitted)
    references = {}
    phase_deadline = load(Path(frozen['plans']['first_dispatch']))['deadline_ns']
    for name, timeout in plans.items():
        references[name] = dict(timeout_ns=timeout, conditions={})
        for condition, messages in data.items():
            pending, ambiguous = [], []
            for identity, value in sorted(messages.items()):
                deadline = value['post_ns']+timeout
                if value['ack_ns'] > deadline:
                    pending.append(dict(message=identity, send_post_ns=value['post_ns'], deadline_ns=deadline))
                elif value['ack_ns'] == deadline:
                    ambiguous.append(identity)
            earliest = min((r['deadline_ns'] for r in pending), default=None)
            references[name]['conditions'][condition] = dict(considerations=pending, count=len(pending),
                same_time_ambiguous=ambiguous, first_nominal_deadline_ns=earliest,
                delay_from_phase_check_ns=None if earliest is None else earliest-phase_deadline)
    result = dict(passed=True, normal_max_post_to_ack_ns=normal_max, normal_calibrated_timeout_ns=fitted,
        references=references, actual_notifications_generated=0, new_network_executions=0,
        complete_call_watchdog_integration_implemented=False, recovery_benefit_measured=False,
        source_sha256={str(p.relative_to(root)): sha(p) for p in (code, documentation, Path(__file__).with_name('watchdog_protocol.md'), Path(__file__))},
        scope='Reference predicates over first Dispatch only; no actual watchdog in saved controls, no new notification, no action, no full-service notification budget validation. Whole-call currently rejects watchdog combination.')
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
