"""Independent Floyd-Warshall distances and exhaustive old-forwarding DAG checks."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(topology, plan, summary):
    nodes, links = topology['nodes'], topology['links']
    count = len(nodes)
    gpus = [n['id'] for n in nodes if n['kind'] == 'gpu']
    switches = [n['id'] for n in nodes if n['kind'] != 'gpu']
    assert len(gpus) == 128 and len(switches) == 20
    protected = tuple(summary['protected_edge'])
    assert protected == (128, 132)
    adj = [[] for _ in nodes]
    ports = {}
    for e in links:
        for a, p, b, q in ((e['src'], e['src_port'], e['dst'], e['dst_port']),
                            (e['dst'], e['dst_port'], e['src'], e['src_port'])):
            adj[a].append((b, p, q)); ports[a, b] = dict(src=a, src_port=p, dst=b, dst_port=q)
    inf = 10000
    def matrix(failed=False):
        d = [[0 if i == j else inf for j in range(count)] for i in range(count)]
        for e in links:
            a, b = e['src'], e['dst']
            if not failed or (a, b) != protected:
                d[a][b] = d[b][a] = 1
        # GPUs are endpoints, never permitted as Floyd-Warshall intermediates.
        for k in switches:
            for i in range(count):
                if d[i][k] >= inf:
                    continue
                via, row, middle = d[i][k], d[i], d[k]
                for j in range(count):
                    if via+middle[j] < row[j]:
                        row[j] = via+middle[j]
        return d
    old, failed = matrix(), matrix(True)

    def successors(node, dst):
        return [(peer, port, back) for peer, port, back in adj[node] if old[peer][dst]+1 == old[node][dst]]

    def suffix(node, dst, source):
        queue, visited, edges = [node], set(), set()
        safe = True
        while queue:
            at = queue.pop()
            if at == source:
                safe = False
            if at == dst or at in visited:
                continue
            visited.add(at)
            hops = successors(at, dst)
            if not hops:
                safe = False
            for peer, port, back in hops:
                if tuple(sorted((at, peer))) == protected:
                    safe = False
                edges.add((at, port, peer, back))
                queue.append(peer)
        return safe, edges

    rows = plan['destinations']
    assert [(r['source'], r['destination']) for r in rows] == [(s, d) for s in protected for d in gpus]
    states, prefixes, distances = Counter(), 0, 0
    for r in rows:
        source, dst = r['source'], r['destination']
        assert r['original_hops'] == (old[source][dst] if old[source][dst] < inf else -1)
        primary = [dict(peer=n, port=p) for n, p, _ in successors(source, dst)]
        assert primary == r['primary_next_hops']
        alive = [h for h in primary if tuple(sorted((source, h['peer']))) != protected]
        assert alive == r['surviving_primary_next_hops']
        if alive:
            assert r['state'] == ('primary_survives' if len(alive) < len(primary) else 'unaffected')
        else:
            lfas = []
            candidates = []
            for n, port, _ in adj[source]:
                if n in gpus or tuple(sorted((source, n))) == protected:
                    continue
                left, to_source, via = old[n][dst], old[n][source], old[n][source]+old[source][dst]
                available = left < inf and to_source < inf and left < via
                candidates.append(dict(peer=n, port=port, neighbor_to_destination_hops=left if left < inf else -1,
                    neighbor_to_source_hops=to_source if to_source < inf else -1, via_source_hops=via,
                    loop_free=available))
                if available:
                    lfas.append(n)
                    assert suffix(n, dst, source)[0]
            assert candidates == r['LFA_candidates']
            assert not lfas  # This particular Clos, not a claim about all topologies.
            assert failed[source][dst] < inf and r['state'] == 'explicit_repair_required'
            path = r['failed_graph_path']
            assert len(path) == len(set(path)) and path[0] == source and path[-1] == dst
            assert len(path)-1 == r['failed_graph_hops'] == failed[source][dst]
            for a, b in zip(path, path[1:]):
                assert (a, b) in ports and tuple(sorted((a, b))) != protected
                assert failed[b][dst]+1 == failed[a][dst]
                choices = sorted(n for n, _, _ in adj[a]
                                 if tuple(sorted((a, n))) != protected and failed[n][dst]+1 == failed[a][dst])
                assert b == choices[0]
            merge_index = path.index(r['merge'])
            assert merge_index > 0 and r['merge'] in switches
            assert r['repair_prefix_nodes'] == path[:merge_index+1]
            assert r['repair_prefix_edges'] == [ports[a, b] for a, b in zip(path[:merge_index], path[1:merge_index+1])]
            assert all(not suffix(n, dst, source)[0] for n in path[1:merge_index] if n in switches)
            safe, edges = suffix(r['merge'], dst, source)
            assert safe and r['all_old_suffixes_avoid_failed_edge_and_source']
            assert {(e['src'], e['src_port'], e['dst'], e['dst_port']) for e in r['all_old_suffix_edges']} == edges
            prefixes += 1
        states[r['state']] += 1
        distances += 1
    assert dict(states) == summary['coverage'] == plan['summary']['coverage']
    assert plan['summary'] == summary and not summary['network_executed']
    return dict(endpoint_destination_pairs=distances, explicit_prefixes=prefixes, states=dict(states),
                all_old_ECMP_suffixes_checked=True, independent_metric='Floyd-Warshall with switch-only intermediate vertices')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    d = a.stage/'public/repair'
    topology, plan, summary = (load(d/name) for name in ('topology.json', 'plan.json', 'summary.json'))
    manifest = load(d/'manifest.json')
    for name, h in manifest['files_sha256'].items():
        assert sha(d/name) == h
    proof = audit(topology, plan, summary)
    mutations = []
    for label in ('equal-distance accepted as LFA', 'unsafe old ECMP merge', 'wrong physical port', 'invented coverage'):
        changed, cs = deepcopy(plan), deepcopy(summary)
        row = next(r for r in changed['destinations'] if r['source'] == 132 and r['destination'] == 1)
        if label == 'equal-distance accepted as LFA':
            row['LFA_candidates'][0]['loop_free'] = True
        elif label == 'unsafe old ECMP merge':
            row['merge'] = 129
        elif label == 'wrong physical port':
            row['repair_prefix_edges'][0]['src_port'] += 100
        else:
            cs['coverage']['ordinary_LFA_available'] = 16
        try:
            audit(topology, changed, cs)
        except AssertionError:
            mutations.append(dict(mutation=label, rejected=True))
        else:
            raise AssertionError('corrupt plan accepted: '+label)
    result = dict(passed=True, network_executed=False, **proof, tamper_checks=mutations,
        plan_sha256=sha(d/'plan.json'), topology_sha256=sha(d/'topology.json'), verifier_sha256=sha(Path(__file__)))
    with a.output.open('x') as s:
        json.dump(result, s, indent=2); s.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
