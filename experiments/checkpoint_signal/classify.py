"""Judge a configured checkpoint using strict-past observations only.

This process has no simulator action and reads no truth or result file.
"""
import argparse
import json
from pathlib import Path
import re

def load(path):
    return json.loads(path.read_bytes())

def relevant(event, plan, view):
    if event.get('kind') == 'graph_retired' and event.get('graph_id') == plan['final_graph']:
        return True
    checkpoint = plan['checkpoint']
    if checkpoint['kind'] == 'graph_retired':
        return event.get('kind') == 'graph_retired' and event.get('graph_id') == checkpoint['graph']
    members = checkpoint['members'] if view == 'all_members' else [checkpoint['representative']]
    return (event.get('kind') == 'node_complete' and event.get('graph_id') == checkpoint['graph'] and
            any((event.get('rank'), event.get('node')) == (m['rank'], m['node']) for m in members))

def judge(plan, events, view):
    if view not in ('all_members', 'local_member', 'final_only'):
        raise ValueError('unknown observation view')
    deadline = plan['deadline_ns']
    if type(deadline) is not int or deadline <= 0:
        raise ValueError('invalid decision deadline')
    checkpoint = plan['checkpoint']
    previous_time = previous_sequence = -1
    count = 0
    completed = set()
    final_done = step_done = False
    for event in events:
        if event.get('schema_version') != 'moe-execution-event/1' or event.get('type') != 'OBSERVE':
            raise ValueError('invalid observation identity')
        now, sequence = event.get('time_ns'), event.get('sequence')
        if (type(now) is not int or type(sequence) is not int or sequence < 0 or
                not previous_time <= now < deadline or sequence <= previous_sequence):
            raise ValueError('future, same-time, duplicate or out-of-order observation')
        if view != 'local_member' and sequence != count:
            raise ValueError('missing observation in complete prefix')
        graph = event.get('graph_id')
        if graph is not None and (not isinstance(graph, str) or not re.fullmatch(r'g[0-9]+\.w[0-9]+', graph)):
            raise ValueError('invalid graph identity')
        if event['kind'].startswith(('node_', 'message_')):
            rank = event.get('rank')
            if type(rank) is not int or not 0 <= rank < plan['simulated_endpoints']:
                raise ValueError('invalid physical endpoint')
        if event['kind'].startswith('node_'):
            if type(event.get('node')) is not int or event['node'] < 0:
                raise ValueError('invalid node identity')
        if event['kind'] == 'graph_retired':
            final_done |= graph == plan['final_graph']
            step_done |= checkpoint['kind'] == 'graph_retired' and graph == checkpoint['graph']
        if event['kind'] == 'node_complete' and graph == checkpoint['graph']:
            key = (event['rank'], event['node'])
            members = checkpoint.get('members', [])
            if any(key == (m['rank'], m['node']) for m in members):
                if key in completed:
                    raise ValueError('duplicate checkpoint completion')
                completed.add(key)
        previous_time, previous_sequence = now, sequence
        count += 1
    if view == 'final_only':
        done = final_done
    elif checkpoint['kind'] == 'graph_retired':
        done = step_done
    else:
        wanted = checkpoint['members'] if view == 'all_members' else [checkpoint['representative']]
        done = all((m['rank'], m['node']) in completed for m in wanted)
    return dict(schema_version='checkpoint-consideration/1', checkpoint_done=done,
        whole_done=final_done, consider_backup=not (done or final_done),
        action='skip_after_whole_result' if final_done else 'defer_needs_later_check' if done else 'consider_subject_to_budget',
        deadline_ns=deadline, parsed_past_events=count, view=view,
        network_executed=False, recovery_benefit_measured=False)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--view', choices=('all_members', 'local_member', 'final_only'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    events = (json.loads(line) for line in args.events.read_text().splitlines())
    result = judge(load(args.plan), events, args.view)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
