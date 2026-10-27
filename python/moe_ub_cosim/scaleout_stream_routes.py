"""Pure admission for physical persistent endpoint NICs.

Matches Fabric's unweighted per-plane reverse BFS, optionally restricted to
the destination NIC. GPUs are terminal endpoints. Local address binding may
select a longer source attachment; the switch path must still exist.
"""
from collections import defaultdict, deque

from .scaleout_topology import require


class InitialNicRoutes:
    def __init__(self, topology):
        self.nodes = {n['id']: n for n in topology['nodes']}
        self.adjacency = {n: [] for n in self.nodes}
        for link in topology['links']:
            self.adjacency[link['src']].append((link['dst'], link['src_port'], link['dst_port']))
            self.adjacency[link['dst']].append((link['src'], link['dst_port'], link['src_port']))
        self.cache_key = None
        self.distance = {}

    def _distances(self, destination, plane, destination_nic=None):
        key = destination, plane, destination_nic
        if key != self.cache_key:
            distance = {destination: 0}
            queue = deque([destination])
            while queue:
                current = queue.popleft()
                for peer, port, peer_port in self.adjacency[current]:
                    if destination_nic is not None and (
                            (current == destination and port != destination_nic) or
                            (peer == destination and peer_port != destination_nic)):
                        continue
                    node = self.nodes[peer]
                    if node['kind'] != 'gpu' and node['plane'] != plane:
                        continue
                    if peer not in distance:
                        distance[peer] = distance[current]+1
                        if node['kind'] != 'gpu':
                            queue.append(peer)
            # Validation groups requests by destination NIC. Keep one distance map,
            # so admitting many destinations does not retain O(GPU * nodes).
            self.cache_key, self.distance = key, distance
        return self.distance

    def candidates(self, source, destination, plane):
        """Legacy whole-GPU shortest first hops; not the binding policy."""
        distance = self._distances(destination, plane)
        if source == destination or source not in distance:
            return ()
        return tuple(sorted(port for peer, port, _ in self.adjacency[source]
            if distance.get(peer) == distance[source]-1 and
            (self.nodes[peer]['kind'] == 'gpu' or self.nodes[peer]['plane'] == plane)))

    def reachable(self, source, source_nic, destination, destination_nic, plane):
        distance = self._distances(destination, plane, destination_nic)
        return source != destination and any(port == source_nic and peer in distance and
            self.nodes[peer]['kind'] != 'gpu' and self.nodes[peer]['plane'] == plane
            for peer, port, _ in self.adjacency[source])


def validate_nic_routes(topology, streams):
    routes = InitialNicRoutes(topology)
    groups = defaultdict(list)
    for stream in streams:
        for direction, source, source_nic, destination, destination_nic in (
            ('source', stream['src'], stream['source_nic'], stream['dst'], stream['destination_nic']),
            ('reverse', stream['dst'], stream['destination_nic'], stream['src'], stream['source_nic'])):
            groups[destination, stream['plane'], destination_nic].append((stream['id'], direction, source, source_nic))
    for (destination, plane, destination_nic), requests in groups.items():
        for identity, direction, source, source_nic in requests:
            require(routes.reachable(source, source_nic, destination, destination_nic, plane),
                f"persistent stream {identity} {direction} {source}:{source_nic}->{destination}:{destination_nic} "
                f"plane {plane} has no switch path between declared physical NICs")
