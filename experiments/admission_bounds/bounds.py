"""Conservative generic AND/OR timing bounds; never reads future execution.

This is ordinary event-graph lookahead, not a MoE-specific algorithm. Local
contention, serialization, receive predicates and provider backpressure are
relaxed, so finite predictions are lower bounds, not completion estimates.
"""
from collections import defaultdict
import heapq
import math


def require(condition, message):
    if not condition:
        raise ValueError(message)


def earliest(rules):
    """Earliest grounded solution of OR rules, each with AND prerequisites.

    A rule (target, base, [(predecessor, nonnegative_lag), ...]) produces
    max(base, predecessor times + lags). Unreachable positive cycles stay inf.
    Dijkstra settlement is valid because every prerequisite time <= candidate.
    """
    waiting, counts, maxima = defaultdict(list), [], []
    heap, nodes = [], set()
    for index, (target, base, deps) in enumerate(rules):
        require(type(base) is int and base >= 0, 'invalid rule base')
        combined = {}
        for node, lag in deps:
            require(type(lag) is int and lag >= 0, 'negative/noninteger lag')
            combined[node] = max(combined.get(node, 0), lag)
        nodes.add(target)
        nodes.update(combined)
        counts.append(len(combined))
        maxima.append(base)
        for node, lag in combined.items():
            waiting[node].append((index, lag))
        if not combined:
            heapq.heappush(heap, (base, target))
    settled = {}
    while heap:
        time, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled[node] = time
        for index, lag in waiting[node]:
            counts[index] -= 1
            maxima[index] = max(maxima[index], time + lag)
            if counts[index] == 0:
                heapq.heappush(heap, (maxima[index], rules[index][0]))
    return {node: settled.get(node, math.inf) for node in nodes}


def propagation(topology, connection, reverse=False):
    """Shortest possible propagation with the two explicitly bound NICs."""
    src, dst = connection['src'], connection['dst']
    sp, dp = connection['source_nic'], connection['destination_nic']
    if reverse:
        src, dst, sp, dp = dst, src, dp, sp
    require(src != dst, 'self service has no network path')
    nodes = {n['id']: n for n in topology['nodes']}
    adj = defaultdict(list)
    for link in topology['links']:
        for a, b, ap, bp in ((link['src'], link['dst'], link['src_port'], link['dst_port']),
                              (link['dst'], link['src'], link['dst_port'], link['src_port'])):
            if nodes[a]['kind'] == 'gpu' and (a != src or ap != sp):
                continue
            if nodes[b]['kind'] == 'gpu' and (b != dst or bp != dp):
                continue
            if any(nodes[n]['kind'] != 'gpu' and nodes[n]['plane'] != connection['plane'] for n in (a, b)):
                continue
            require(type(link['delay_ns']) is int and link['delay_ns'] >= 0, 'invalid propagation')
            adj[a].append((b, link['delay_ns']))
    heap, seen = [(0, src)], set()
    while heap:
        time, node = heapq.heappop(heap)
        if node in seen:
            continue
        if node == dst:
            return time
        seen.add(node)
        for other, delay in adj[node]:
            heapq.heappush(heap, (time + delay, other))
    raise ValueError('no path through declared NIC/plane')


def submit_node(identity):
    return 's:' + str(identity)


def callback_node(identity, event):
    return f'c:{identity}:{event}'


class AdmissionBounds:
    def __init__(self, graph, program, topology):
        self.graph, self.program = graph, program
        self.work = {op['id']: op for op in program['operations']}
        self.connections = {c['id']: c for c in program['connections']}
        self.paths = {c['id']: (propagation(topology, c), propagation(topology, c, True))
                      for c in program['connections'] if c['src'] != c['dst']}

    def predict(self, controller, mode):
        require(mode in ('causal', 'propagation', 'declared'), 'unknown bound mode')
        now = controller.now
        rules = []
        known = {'e:' + row['event']: row['time_ns'] for row in controller.log}
        known.update({submit_node(row['operation']): row['time_ns'] for row in controller.submission_log})
        known.update({callback_node(i, e): t for (i, e), t in controller.seen.items()})

        def rule(target, deps=()):
            if target not in known:
                rules.append((target, now, list(deps)))

        callbacks = []
        for op in self.work.values():
            identity = op['id']
            s = submit_node(identity)
            def cb(event, deps):
                name = callback_node(identity, event)
                callbacks.append((identity, event, name))
                rule(name, deps)
                return name
            if op['kind'] == 'local':
                admitted = cb('local_admitted', [(s, 0)])
                started = cb('local_started', [(admitted, 0)])
                cb('completed', [(started, op['duration_ns'] if mode == 'declared' else 0)])
            else:
                connection = self.connections[op['connection']]
                submitted = cb('submitted', [(s, 0)])
                prepared = cb('prepared', [(submitted, 0)])
                published = cb('published', [(prepared, 0)])
                if connection['src'] == connection['dst']:
                    costs = connection['self_service']
                    applied = cb('applied', [(published, costs['apply_ns'] if mode == 'declared' else 0)])
                    cb('completed', [(applied, 0), (published, costs['complete_ns'] if mode == 'declared' else 0)])
                else:
                    forward, backward = self.paths[connection['id']] if mode != 'causal' else (0, 0)
                    applied = cb('applied', [(published, forward)])
                    acked = cb('request_acked', [(applied, backward)])
                    done_deps = [(acked, 0)]
                    if op['verb'] == 'fetch_add':
                        response = cb('response', [(applied, backward)])
                        done_deps.append((response, 0))
                    cb('completed', done_deps)

        for index, event in enumerate(self.graph['events']):
            parents = [('e:' + p, 0) for p in event['deps']]
            waits, gates = [], []
            for action_index, action in enumerate(event['actions']):
                waits.extend((callback_node(*pair), 0) for pair in action['wait_before'])
                gate = f'a:{index}:{action_index}'
                native = self.work[action['work']]
                proof = [(callback_node(p['operation'], p['event']), 0) for p in native['after']]
                rule(gate, parents + waits + proof)
                rule(submit_node(action['work']), [(gate, 0)])
                gates.append((gate, 0))
            callback = [] if event['callback'] is None else [(callback_node(*event['callback']), 0)]
            rule('e:' + event['id'], parents + gates + callback)
        rules.extend((name, time, []) for name, time in known.items())
        times = earliest(rules)

        def value(name):
            result = times.get(name, math.inf)
            return None if math.isinf(result) else result
        work_bounds = [[identity, value(submit_node(identity))] for identity in sorted(self.work)
                       if identity not in controller.submitted]
        require(all(t is not None for _, t in work_bounds), 'pending work unreachable in lower-bound graph')
        callback_bounds = [[identity, event, value(node)] for identity, event, node in callbacks
                           if (identity, event) not in controller.seen]
        require(all(t is not None for _, _, t in callback_bounds), 'callback unreachable in lower-bound graph')
        scopes = {'all_work': [identity for identity, _ in work_bounds], 'wire': []}
        for c in self.connections.values():
            if c['src'] != c['dst']:
                scopes.setdefault('plane:' + str(c['plane']), [])
        for identity, _ in work_bounds:
            op = self.work[identity]
            if op['kind'] == 'remote':
                c = self.connections[op['connection']]
                if c['src'] != c['dst']:
                    scopes['wire'].append(identity)
                    scopes['plane:' + str(c['plane'])].append(identity)
        by_id = dict(work_bounds)
        return dict(next={scope: min((by_id[i] for i in ids), default=None) for scope, ids in scopes.items()},
                    work=work_bounds, callbacks=callback_bounds)
