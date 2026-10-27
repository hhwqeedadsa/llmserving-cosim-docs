"""Plan already-queued switch prefixes from a chronological capture prefix.

Conservative local scheduling baseline, not a native event coalescer. The
caller supplies only records seen so far; no future TX table is available.
"""
from collections import defaultdict, deque


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identity(row):
    return tuple(row[i] for i in (4, 6, 7, 8, 11))


def service(size, rate):
    return (size * 8 * 10**9 + rate//2)//rate


def select_prefix(first, queued, now, rate, deadline):
    selected, cursor = [[list(first[0]), now, first[1]]], now + service(first[1], rate)
    for pid, size in queued:
        finish = cursor + service(size, rate)
        if deadline is not None and finish >= deadline:
            break
        selected.append([list(pid), cursor, size])
        cursor = finish
    return selected, cursor


class PrefixPlanner:
    def __init__(self, topology, protocol):
        self.nodes = {n['id']: n['kind'] for n in topology['nodes']}
        self.peers, self.delays, self.kmin = {}, defaultdict(list), {}
        for link in topology['links']:
            require(link['delay_ns'] >= 0 and link['rate_bps'] > 0, 'invalid link')
            for a, b, ap, bp in ((link['src'], link['dst'], link['src_port'], link['dst_port']),
                                 (link['dst'], link['src'], link['dst_port'], link['src_port'])):
                require((a, ap) not in self.peers, 'duplicate physical port')
                self.peers[a, ap] = b, bp, link['rate_bps'], link['delay_ns']
                self.delays[a].append(link['delay_ns'])
                self.kmin[a, ap] = protocol['ecn_by_rate'][str(link['rate_bps'])]['kmin_bytes']
        self.queues, self.bytes = defaultdict(deque), defaultdict(int)
        self.inflight, self.dequeued = {}, {}
        self.claimed = {mode: set() for mode in ('conservative', 'topology_only', 'queued_upper')}
        self.snapshots = []
        self.now = 0

    def observe(self, row, ordinal):
        now, node, port, event = row[:4]
        require(now >= self.now, 'nonmonotone observed capture')
        self.now = now
        require(row[6] in (17, 252), 'feedback/fault records outside this initial planner')
        pid, priority = identity(row), 0 if row[6] == 252 else 3
        key = node, port, priority
        if event == 3:
            require(self.nodes[node] != 'gpu', 'unsupported NIC queue hook')
            self.queues[key].append((pid, row[8]))
            self.bytes[key] += row[8]
            require(sum(self.bytes[node, port, q] for q in (0, 3)) == row[10], 'enqueue occupancy mismatch')
        elif event == 4:
            require(self.queues[key] and self.queues[key][0] == (pid, row[8]), 'queue FIFO identity mismatch')
            require(priority == 0 or not self.queues[node, port, 0], 'ACK priority violation')
            require(sum(self.bytes[node, port, q] for q in (0, 3)) == row[10], 'dequeue occupancy mismatch')
            self.queues[key].popleft()
            self.bytes[key] -= row[8]
            self.dequeued[node, port] = now, pid
        elif event == 1:
            arrival_key = node, port, pid
            require(arrival_key in self.inflight and self.inflight[arrival_key] == now,
                    'receive without matching observed transmission')
            del self.inflight[arrival_key]
        elif event == 0:
            peer, peer_port, rate, delay = self.peers[node, port]
            target = peer, peer_port, pid
            require(target not in self.inflight, 'duplicate inflight transmission')
            self.inflight[target] = now + service(row[8], rate) + delay
            if self.nodes[node] == 'gpu':
                return []
            require(self.dequeued.pop((node, port), None) == (now, pid), 'TX lacks its current dequeue')
            topology_cut = now + min(self.delays[node])
            known_arrivals = [time for (dst, _, _), time in self.inflight.items() if dst == node]
            inflight_cut = min(known_arrivals, default=None)
            require(inflight_cut is None or inflight_cut >= now, 'unobserved overdue receive')
            deadline = min(topology_cut, inflight_cut) if inflight_cut is not None else topology_cut
            # At this hook the current packet is no longer charged in q3.
            below_mark_threshold = priority == 0 or self.bytes[key] <= self.kmin[node, port]
            self.snapshots.append(dict(node=node, port=port, priority=priority, ordinal=ordinal,
                packet=list(pid), queued_prefix=[list(pid), *[list(p[0]) for p in self.queues[key]]]))
            records = []
            for mode, cut in (('conservative', deadline), ('topology_only', topology_cut), ('queued_upper', None)):
                if (node, port, pid) in self.claimed[mode]:
                    continue
                candidates = []
                if below_mark_threshold:
                    for candidate in self.queues[key]:
                        if (node, port, candidate[0]) in self.claimed[mode]:
                            break
                        candidates.append(candidate)
                selected, finish = select_prefix((pid, row[8]), candidates, now, rate, cut)
                self.claimed[mode].update((node, port, tuple(p[0])) for p in selected)
                records.append(dict(mode=mode, ordinal=ordinal, node=node, port=port, priority=priority,
                    now_ns=now, deadline_ns=cut, topology_cut_ns=topology_cut,
                    earliest_observed_inflight_RX_ns=inflight_cut,
                    below_mark_threshold=below_mark_threshold,
                    selected=selected, finish_ns=finish))
            return records
        else:
            raise ValueError('drop or unsupported capture event')
        return []
