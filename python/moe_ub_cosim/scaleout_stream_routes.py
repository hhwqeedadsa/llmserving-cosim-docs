"""Pure admission for the fixed persistent transport's initial NIC routes.

Matches Fabric::RebuildRoutes' unweighted, per-plane reverse BFS: GPU nodes
are terminal endpoints. This validates an existing restriction, and never
chooses an ECMP hash, changes forwarding, or predicts failure-time routes.
"""
from collections import defaultdict, deque

from .scaleout_topology import require


class InitialNicRoutes:
    def __init__(self, topology):
        self.nodes = {n['id']: n for n in topology['nodes']}
        self.adjacency = {n: [] for n in self.nodes}
        for link in topology['links']:
            self.adjacency[link['src']].append((link['dst'], link['src_port']))
            self.adjacency[link['dst']].append((link['src'], link['dst_port']))
        self.cache_key = None
        self.distance = {}

    def candidates(self, source, destination, plane):
        key = destination, plane
        if key != self.cache_key:
            distance = {destination: 0}
            queue = deque([destination])
            while queue:
                current = queue.popleft()
                for peer, _ in self.adjacency[current]:
                    node = self.nodes[peer]
                    if node['kind'] != 'gpu' and node['plane'] != plane:
                        continue
                    if peer not in distance:
                        distance[peer] = distance[current]+1
                        if node['kind'] != 'gpu':
                            queue.append(peer)
            # Validation groups requests by destination. Keep one distance map,
            # so admitting many destinations does not retain O(GPU * nodes).
            self.cache_key, self.distance = key, distance
        distance = self.distance
        if source == destination or source not in distance:
            return ()
        return tuple(sorted(port for peer, port in self.adjacency[source]
            if distance.get(peer) == distance[source]-1 and
            (self.nodes[peer]['kind'] == 'gpu' or self.nodes[peer]['plane'] == plane)))


def validate_nic_routes(topology, streams):
    routes = InitialNicRoutes(topology)
    groups = defaultdict(list)
    for stream in streams:
        for direction, source, destination, declared in (
            ('source', stream['src'], stream['dst'], stream['source_nic']),
            ('reverse', stream['dst'], stream['src'], stream['destination_nic'])):
            groups[destination, stream['plane']].append((stream['id'], direction, source, declared))
    for (destination, plane), requests in groups.items():
        for identity, direction, source, declared in requests:
            candidates = routes.candidates(source, destination, plane)
            require(candidates == (declared,),
                f"persistent stream {identity} {direction} {source}->{destination} "
                f"plane {plane} requires unique shortest NIC {declared}; "
                f"candidates {list(candidates)}. Same-plane multi-NIC routing needs "
                "explicit per-connection native binding; it is not implemented by this transport")
