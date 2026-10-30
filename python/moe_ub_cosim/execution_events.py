"""Passive, bounded actual-node and message lifecycle observations.

This does not accept results, schedule helpers, or authenticate tensor identity.
Receiver delivery to ASTRA and sender confirmation are independent milestones.
"""
import json
from pathlib import Path

from .scaleout_topology import fields, integer, require


def configuration(config):
    value = config.get('execution_events')
    if 'execution_events' not in config:
        return None
    fields(value, 'schema_version max_events', 'execution_events')
    require(value['schema_version'] == 'moe-execution-events/1', 'unsupported execution events schema')
    integer(value['max_events'], 'execution max_events', 1, 262144)
    return value


class ExecutionObserver:
    """Consume synchronous callbacks without issuing any simulation action."""
    def __init__(self, config):
        self.config = dict(config)
        configuration(dict(execution_events=config))
        self.count = 0
        self.now = -1
        self.nodes = {}
        self.messages = {}
        self.graphs = {}
        self.drained_ns = None

    def register(self, wave):
        graph = wave['graph_id']
        require(self.drained_ns is None and graph not in self.graphs, 'reused or post-drain observed graph')
        nodes = {(graph, int(rank), n['id']): n for rank, tasks in wave['rank_nodes'].items() for n in tasks}
        messages = {m['id']: m for m in wave['messages']}
        require(len(messages) == len(wave['messages']) and not (messages.keys() & self.messages.keys()),
                'reused observed message')
        minimum = 2*(len(self.nodes)+len(nodes)) + 4*(len(self.messages)+len(messages)) + len(self.graphs)+2
        require(minimum <= self.config['max_events'], 'declared graph exceeds execution event budget')
        bindings = {}
        for key, node in nodes.items():
            if node['kind'] in ('SEND', 'RECV'):
                binding = node['message']['id'], node['kind'].lower()
                require(binding not in bindings, 'duplicate observed message node')
                bindings[binding] = key
        require(set(bindings) == {(mid, side) for mid in messages for side in ('send', 'recv')},
                'missing observed message node')
        self.graphs[graph] = dict(nodes=set(nodes), messages=set(messages), retired_ns=None)
        for key, node in nodes.items():
            self.nodes[key] = dict(spec=node, issue=None, complete=None)
        for mid, message in messages.items():
            self.messages[mid] = dict(spec=message, graph=graph,
                                     send_post=None, recv_post=None, send_complete=None, recv_complete=None,
                                     send_node=bindings[mid, 'send'], recv_node=bindings[mid, 'recv'])

    def accept(self, event):
        require(isinstance(event, dict), 'invalid execution event')
        kind = event.get('kind')
        base = 'schema_version type sequence time_ns kind'
        if kind in ('node_issue', 'node_complete'):
            extra = ' graph_id rank node'
        elif kind in ('message_send_post', 'message_recv_post', 'message_send_complete', 'message_recv_complete'):
            extra = ' graph_id rank message'
        elif kind == 'graph_retired':
            extra = ' graph_id'
        else:
            require(kind == 'service_drained', 'unknown execution event kind')
            extra = ''
        fields(event, base+extra, 'execution event')
        require(event['schema_version'] == 'moe-execution-event/1' and event['type'] == 'OBSERVE',
                'invalid execution event schema/type')
        integer(event['sequence'], 'execution sequence', 0, 262143)
        integer(event['time_ns'], 'execution time_ns', 0, 2**63-1)
        require(self.drained_ns is None and event['sequence'] == self.count and
                self.count < self.config['max_events'] and event['time_ns'] >= self.now,
                'execution event order/budget/drain violation')
        now = event['time_ns']
        if extra:
            graph = event['graph_id']
            require(isinstance(graph, str) and graph in self.graphs, 'unknown observed graph')
            group = self.graphs[graph]
            require(group['retired_ns'] is None, 'event after observed graph retirement')
        if kind.startswith(('node_', 'message_')):
            integer(event['rank'], 'execution rank', 0, 511)
        if kind.startswith('node_'):
            integer(event['node'], 'execution node', 0, 2**64-1)
            key = graph, event['rank'], event['node']
            require(key in self.nodes, 'unknown observed node')
            state = self.nodes[key]
            spec = state['spec']
            action = kind.removeprefix('node_')
            require(state[action] is None, 'duplicate observed node event')
            if action == 'issue':
                require(all(self.nodes[graph, event['rank'], d]['complete'] is not None for d in spec['deps']),
                        'observed node issued before dependencies')
            else:
                require(state['issue'] is not None, 'observed node completion before issue')
                if spec['kind'] in ('SEND', 'RECV'):
                    side = 'send' if spec['kind'] == 'SEND' else 'recv'
                    require(self.messages[spec['message']['id']][side+'_complete'] is not None,
                            'node completed before actual message callback')
                else:
                    require(now-state['issue'] >= spec['duration_ns'], 'node completed before declared work')
            state[action] = now
        elif kind.startswith('message_'):
            integer(event['message'], 'execution message', 0, 2**64-1)
            mid = event['message']
            require(mid in group['messages'], 'foreign observed message')
            state = self.messages[mid]
            spec = state['spec']
            action = kind.removeprefix('message_')
            side, milestone = action.split('_', 1)
            require(event['rank'] == spec['src' if side == 'send' else 'dst'] and state[action] is None,
                    'wrong rank or duplicate observed message event')
            if milestone == 'complete':
                require(state[side+'_post'] is not None and state['send_post'] is not None,
                        'message callback before actual post')
            else:
                bound = self.nodes[state[side+'_node']]
                require(bound['issue'] is not None and bound['complete'] is None,
                        'message post without issued ASTRA node')
            state[action] = now
        elif kind == 'graph_retired':
            require(all(self.nodes[k]['complete'] is not None for k in group['nodes']) and
                    all(self.messages[m]['send_complete'] is not None and
                        self.messages[m]['recv_complete'] is not None for m in group['messages']),
                    'observed graph retired with live nodes/messages')
            group['retired_ns'] = now
        else:
            require(bool(self.graphs) and all(g['retired_ns'] is not None for g in self.graphs.values()),
                    'service drained before observed graph retirement')
            self.drained_ns = now
        self.count += 1
        self.now = now

    def snapshot(self):
        return dict(schema_version='moe-execution-observation/1', events=self.count,
                    last_time_ns=self.now if self.count else None, service_drained_ns=self.drained_ns,
                    registered_graphs=len(self.graphs), retired_graphs=sum(g['retired_ns'] is not None for g in self.graphs.values()),
                    nodes=len(self.nodes), node_completions=sum(n['complete'] is not None for n in self.nodes.values()),
                    messages=len(self.messages), receiver_completions=sum(m['recv_complete'] is not None for m in self.messages.values()),
                    sender_completions=sum(m['send_complete'] is not None for m in self.messages.values()),
                    scope='passive_actual_callbacks_no_recovery_or_tensor_validation')


def audit(directory, result, frontend):
    """Recheck the online stream against independent logs and result callbacks."""
    from .scaleout_rdma import checked_json
    from .scaleout_verify import node_events
    directory = Path(directory)
    config = checked_json(directory/'input.json', 2**20)
    option = configuration(config)
    native = result['service'].get('execution_events')
    if option is None:
        require(native is None and not (directory/'execution_events.jsonl').exists(), 'undeclared execution observations')
        return None
    require(native is not None and native['schema_version'] == option['schema_version'] and
            native['max_events'] == option['max_events'], 'effective execution event config differs')
    observer = ExecutionObserver(option)
    for row in frontend['history']:
        observer.register(checked_json(directory/'graphs'/row['graph_id']/'wave.json', 268435456))
    logs = node_events(directory)
    callbacks = {m['id']: m for m in result['service']['message_callbacks']}
    retired = {w['graph_id']: w['finished_ns'] for w in result['service']['waves']}
    path = directory/'execution_events.jsonl'
    require(path.stat().st_size <= option['max_events']*1024, 'execution event file exceeds byte budget')
    with path.open() as stream:
        for line in stream:
            require(line.endswith('\n') and len(line.encode()) <= 1024, 'truncated/oversized execution event')
            event = json.loads(line)
            observer.accept(event)
            kind = event['kind']
            if kind.startswith('node_'):
                key = event['graph_id'], event['rank'], event['node'], 'issue' if kind == 'node_issue' else 'callback'
                require(logs.get(key) == event['time_ns'], 'live node event differs from independent ASTRA log')
            elif kind.startswith('message_'):
                column = dict(message_send_post='send_post_ns', message_recv_post='recv_post_ns',
                              message_send_complete='send_callback_ns', message_recv_complete='recv_callback_ns')[kind]
                require(callbacks[event['message']][column] == event['time_ns'], 'live message event differs from actual callback')
            elif kind == 'graph_retired':
                require(retired[event['graph_id']] == event['time_ns'], 'live graph retirement differs')
    summary = observer.snapshot()
    require(observer.drained_ns == result['finish_ns'] and native['events'] == observer.count and
            checked_json(directory/'execution_observation.json', 65536) == summary,
            'incomplete or mismatched live execution observations')
    return summary
