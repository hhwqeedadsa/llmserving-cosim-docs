"""Static link-protection coverage; no packet, controller or GPU execution."""
from collections import Counter, deque
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from .scaleout_topology import integer, require


class RoutingGraph:
    """Unit hop metrics with GPU leaves never used as transit routers."""
    def __init__(self, topology):
        self.nodes = topology['nodes']
        self.adj = [[] for _ in self.nodes]
        self._distance_cache = {}
        for edge in topology['links']:
            a, b = edge['src'], edge['dst']
            self.adj[a].append((b, edge['src_port'], edge['dst_port']))
            self.adj[b].append((a, edge['dst_port'], edge['src_port']))

    def distances(self, root, failed=None):
        key = root, failed
        if key in self._distance_cache:
            return self._distance_cache[key]
        distance = [-1]*len(self.nodes)
        distance[root] = 0
        queue = deque([root])
        while queue:
            node = queue.popleft()
            for peer, _, _ in self.adj[node]:
                if failed is not None and tuple(sorted((node, peer))) == failed:
                    continue
                if distance[peer] == -1:
                    distance[peer] = distance[node]+1
                    if self.nodes[peer]['kind'] != 'gpu':
                        queue.append(peer)
        self._distance_cache[key] = tuple(distance)
        return self._distance_cache[key]

    def next_hops(self, node, target):
        distance = self.distances(target)
        if distance[node] < 1:
            return []
        return [(peer, port, back) for peer, port, back in self.adj[node]
                if distance[peer] == distance[node]-1]

    def plan_destination(self, source, target, failed):
        distance = self.distances(target)
        row = dict(source=source, destination=target, original_hops=distance[source],
                   primary_next_hops=[dict(peer=p, port=o) for p, o, _ in self.next_hops(source, target)])
        if distance[source] < 0:
            return dict(row, state='originally_unreachable')
        survivors = [x for x in row['primary_next_hops'] if tuple(sorted((source, x['peer']))) != failed]
        row['surviving_primary_next_hops'] = survivors
        if survivors:
            return dict(row, state='primary_survives' if len(survivors) < len(row['primary_next_hops']) else 'unaffected')
        # Only now consider a non-primary neighbor. The neighbor's metric uses
        # the OLD graph; a failed-graph BFS here would incorrectly assume that
        # unnotified routers already know the failure.
        candidates = []
        for neighbor, port, _ in self.adj[source]:
            if self.nodes[neighbor]['kind'] == 'gpu' or tuple(sorted((source, neighbor))) == failed:
                continue
            left = distance[neighbor]
            to_source = self.distances(source)[neighbor]
            right = to_source+distance[source]
            candidates.append(dict(peer=neighbor, port=port, neighbor_to_destination_hops=left,
                neighbor_to_source_hops=to_source, via_source_hops=right,
                loop_free=left >= 0 and to_source >= 0 and left < right))
        row['LFA_candidates'] = candidates

        @lru_cache(maxsize=None)
        def old_suffix_safe(node):
            if node == target:
                return True
            if node == source:
                return False
            hops = self.next_hops(node, target)
            return bool(hops) and all(tuple(sorted((node, peer))) != failed and old_suffix_safe(peer)
                                      for peer, _, _ in hops)

        safe = [x for x in candidates if x['loop_free']]
        if safe:
            assert all(old_suffix_safe(x['peer']) for x in safe)
            return dict(row, state='ordinary_LFA_available', LFA_next_hops=safe)
        after = self.distances(target, failed)
        row['failed_graph_hops'] = after[source]
        if after[source] < 0:
            return dict(row, state='partitioned')
        path, edges = [source], []
        at = source
        while at != target:
            choices = [(peer, port, back) for peer, port, back in self.adj[at]
                       if tuple(sorted((at, peer))) != failed and after[peer] == after[at]-1]
            peer, port, back = min(choices)
            edges.append(dict(src=at, src_port=port, dst=peer, dst_port=back))
            path.append(peer)
            at = peer
        assert len(set(path)) == len(path)
        merge_index = next((i for i, node in enumerate(path[1:], 1)
                            if self.nodes[node]['kind'] != 'gpu' and old_suffix_safe(node)), None)
        if merge_index is None:
            return dict(row, state='no_switch_merge', failed_graph_path=path)
        merge = path[merge_index]
        stack, visited, suffix = [merge], set(), []
        while stack:
            node = stack.pop()
            if node == target or node in visited:
                continue
            visited.add(node)
            for peer, port, back in self.next_hops(node, target):
                suffix.append(dict(src=node, src_port=port, dst=peer, dst_port=back))
                stack.append(peer)
        return dict(row, state='explicit_repair_required', failed_graph_path=path,
            repair_prefix_nodes=path[:merge_index+1], repair_prefix_edges=edges[:merge_index], merge=merge,
            all_old_suffix_edges=sorted(suffix, key=lambda x: (x['src'], x['src_port'])),
            all_old_suffixes_avoid_failed_edge_and_source=True)


def validate(topology, source, destination):
    require(isinstance(topology, dict) and topology.get('schema_version') in
            ('moe-scaleout-physical-topology/1', 'moe-scaleout-physical-topology/2'), 'unsupported physical topology')
    nodes, links = topology['nodes'], topology['links']
    require(isinstance(nodes, list) and 0 < len(nodes) <= 768, 'repair planning node budget exceeded')
    require(isinstance(links, list) and len(links) <= 16384, 'repair planning link budget exceeded')
    gpus = sum(n['kind'] == 'gpu' for n in nodes)
    require(gpus in (128, 256, 512) and len(nodes)-gpus <= 256, 'repair planning requires128/256/512 endpoints and <=256 switches')
    for i, n in enumerate(nodes):
        integer(n['id'], 'node id', 0, len(nodes)-1)
        require(n['id'] == i and n['kind'] in ('gpu', 'tor', 'agg', 'core'), 'invalid node ordering or kind')
        require((i < gpus) == (n['kind'] == 'gpu'), 'GPU nodes must precede switches')
        if i >= gpus:
            require(type(n.get('plane')) is int and n['plane'] == 0, 'repair planning supports one plane0')
    integer(source, 'protected source switch', gpus, len(nodes)-1)
    integer(destination, 'protected destination switch', source+1, len(nodes)-1)
    pairs, ports, degree = set(), set(), Counter()
    for e in links:
        a, b = e['src'], e['dst']
        integer(a, 'edge source', 0, len(nodes)-1)
        integer(b, 'edge destination', a+1, len(nodes)-1)
        require((a, b) not in pairs, 'duplicate physical edge')
        pairs.add((a, b))
        require(b >= gpus, 'GPU to GPU physical transit is unsupported')
        allowed = {frozenset(('gpu', 'tor')), frozenset(('tor', 'agg')),
                   frozenset(('agg', 'core')), frozenset(('tor', 'core'))}
        require(frozenset((nodes[a]['kind'], nodes[b]['kind'])) in allowed, 'edge is outside supported Clos tiers')
        for node, key in ((a, 'src_port'), (b, 'dst_port')):
            integer(e[key], 'physical port', 1, 1024)
            require((node, e[key]) not in ports, 'duplicate physical port')
            ports.add((node, e[key])); degree[node] += 1
    require((source, destination) in pairs, 'protected edge must be a real switch-switch link')
    require(all(degree[n] == 1 for n in range(gpus)), 'repair planning requires one physical NIC per GPU')
    return gpus


def plan(topology, source, destination):
    gpus = validate(topology, source, destination)
    graph = RoutingGraph(topology)
    failed = (source, destination)
    rows = [graph.plan_destination(at, dst, failed) for at in failed for dst in range(gpus)]
    counts = Counter(row['state'] for row in rows)
    summary = dict(schema_version='moe-scaleout-repair-plan-summary/1', state='planned_not_executed',
        network_executed=False, gpu_endpoints=gpus, switches=len(topology['nodes'])-gpus,
        protected_edge=list(failed), endpoint_destination_pairs=len(rows), coverage=dict(sorted(counts.items())),
        ordinary_LFA_rule='D(N,D) < D(N,S) + D(S,D), old hop graph, strict inequality',
        assumptions=['single bidirectional switch-link failure', 'single plane and one NIC per GPU',
                     'unit hop metrics as native Fabric, GPU nodes never transit',
                     'explicit prefix requires future native steering and wire overhead; not executed'],
        scope='link protection only; no node protection, controller timing, packet repair or latency prediction')
    return dict(schema_version='moe-scaleout-repair-plan/1', summary=summary, destinations=rows), summary


def execute(topology_directory, source, destination, output):
    path = Path(topology_directory).resolve()/'topology.json'
    require(path.stat().st_size <= 8*2**20, 'physical topology file exceeds8MiB')
    raw = path.read_bytes()
    result, summary = plan(json.loads(raw), source, destination)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output/'topology.json').write_bytes(raw)
    for name, value in [('plan.json', result), ('summary.json', summary)]:
        with (output/name).open('x') as s:
            json.dump(value, s, indent=2); s.write('\n')
    manifest = dict(schema_version='moe-scaleout-repair-plan-manifest/1', network_executed=False,
        files_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()})
    with (output/'manifest.json').open('x') as s:
        json.dump(manifest, s, indent=2); s.write('\n')
    return dict(passed=True, output=str(output), **summary)
