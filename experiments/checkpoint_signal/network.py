"""Explain the first Dispatch signal from its past SEND/RECV callbacks."""
import argparse
from collections import Counter
import json
from pathlib import Path

def load(path):
    return json.loads(path.read_bytes())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    frozen = load(args.prepared/'frozen.json')
    plan = load(Path(frozen['plans']['first_dispatch']))
    conditions = {}
    for condition, record in frozen['records'].items():
        initial = load(Path(record['output'])/'graphs/g0.w0/wave.json')
        messages = {m['id']: m for m in initial['messages'] if m['layer'] == 0 and m['phase'] == 'dispatch'}
        events = [json.loads(line) for line in (args.prepared/'observed'/f'first_dispatch-{condition}-full.jsonl').read_text().splitlines()]
        assert all(e['time_ns'] < plan['deadline_ns'] for e in events)
        callback = {(e['message'], e['kind']): e['time_ns'] for e in events
                    if e.get('graph_id') == 'g0.w0' and e['kind'].startswith('message_') and e['message'] in messages}
        pack_nodes = {(int(rank), n['id']) for rank, nodes in initial['rank_nodes'].items() for n in nodes if n['name'] == 'L0.pack'}
        pack_done = {(e['rank'], e['node']) for e in events if e.get('graph_id') == 'g0.w0' and e['kind'] == 'node_complete'
                     and (e['rank'], e['node']) in pack_nodes}
        rows = []
        for identity, message in sorted(messages.items()):
            rows.append(dict(message=identity, src=message['src'], dst=message['dst'], bytes=message['bytes'],
                send_post_ns=callback.get((identity, 'message_send_post')),
                recv_complete_ns=callback.get((identity, 'message_recv_complete')),
                send_complete_ns=callback.get((identity, 'message_send_complete'))))
        pending = [r for r in rows if r['send_post_ns'] is not None and r['send_complete_ns'] is None]
        conditions[condition] = dict(deadline_ns=plan['deadline_ns'], initial_pack_nodes=len(pack_nodes), completed_pack_nodes=len(pack_done),
            initial_dispatch_messages=len(rows), posted_messages=sum(r['send_post_ns'] is not None for r in rows),
            source_confirmed_messages=sum(r['send_complete_ns'] is not None for r in rows),
            receive_completed_messages=sum(r['recv_complete_ns'] is not None for r in rows),
            pending_source_confirmations=len(pending), pending_destination_counts=dict(Counter(r['dst'] for r in pending)),
            pending_messages=pending)
    result = dict(passed=True, conditions=conditions, new_network_executions=0,
        policy_used_this_information=False,
        scope='Initial graph and strictly past callbacks only. A pending source confirmation is not proof of packet loss: data or ACK congestion can give the same signal. Does not measure hardware completion or execute recovery.')
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
