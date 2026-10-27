"""Evaluate a frozen generic graph against separate detailed public outputs."""
import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def compare(stage, name):
    ledger = load(stage/'execution.json')
    assert len(ledger['records']) == ledger['network_executions'] <= 3
    assert len({r['name'] for r in ledger['records']}) == len(ledger['records'])
    assert next(r['state'] for r in ledger['records'] if r['name'] == name) == 'completed'
    graph_raw = (stage/'event-graph.json').read_bytes()
    assert hashlib.sha256(graph_raw).hexdigest() == ledger['event_graph_sha256']
    graph = json.loads(graph_raw)
    old = load(stage/'reference'/name/'result.json')
    new = load(stage/name/'result.json')
    detailed = load(stage/'reference'/name/'modern-execution.json')
    generic = load(stage/name/'event-execution.json')
    assert old['passed'] and new['passed'] and new['session']['finished'] and generic['all_events_fired']
    assert new['session']['decisions'] <= 256 and generic['logical_steps'] <= 16384
    assert new['packet_capture']['omitted_events'] == old['packet_capture']['omitted_events'] == 0
    fields = ('event', 'time_ns', 'native_operation', 'native_event', 'native_time_ns')
    expected = [{k: row[k] for k in fields if k in row} for row in detailed['events']]
    matches = {key: old[key] == new[key] for key in (
        'transactions', 'local_work', 'packet_capture', 'queue_events', 'queue_observations', 'ports', 'finish_ns')}
    matches['application_events_and_release'] = expected == generic['events']
    matches['native_callbacks'] = old['session']['events'] == new['session']['events']
    registrations = lambda r: [{k: x[k] for k in ('operation', 'time_ns')} for x in r['session']['registrations']]
    matches['native_registration_order_and_time'] = registrations(old) == registrations(new)
    def commits(path):
        return [(r['message']['now_ns'], r['message']['submissions'], r['message']['finish'])
                for line in path.read_text().splitlines() if (r := json.loads(line))['direction'] == 'frontend'
                and (r['message']['submissions'] or r['message']['finish'])]
    matches['productive_commits'] = commits(stage/'reference'/name/'control.jsonl') == commits(stage/name/'control.jsonl')
    event_times = {e['event']: e['time_ns'] for e in generic['events']}
    app = max(event_times[e] for e in graph['application_events'])
    return dict(equivalent=all(matches.values()), checks=matches, name=name,
        application_finish_ns=app, all_events_and_source_release_ns=max(event_times.values()), drain_ns=new['finish_ns'],
        source_packets=len(new['transactions']['transport']['source_packets']),
        captured_physical_events=new['packet_capture']['observations'],
        physical_decisions=new['session']['decisions'], logical_steps=generic['logical_steps'],
        reference_physical_decisions=old['session']['decisions'],
        graph_sha256=ledger['event_graph_sha256'], hardware_calibrated=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--name', choices=('baseline', 'bandwidth', 'propagation'), required=True)
    args = parser.parse_args()
    row = compare(args.stage, args.name)
    save(args.stage/(args.name+'-equivalence.json'), row)
    if not row['equivalent']:
        ledger = load(args.stage/'execution.json')
        ledger.update(closed=True, state='nonequivalence_preserved_no_remaining_networks')
        save(args.stage/'execution.json', ledger)
    print(json.dumps(row))
    assert row['equivalent'], 'preserve mismatch; no rerun or remaining networks'


if __name__ == '__main__':
    main()
