"""Collect hash-checked physical inputs for the offline runtime report.

Reads saved inputs only, including failed runs. Never infers connectivity from
counts or presents a computed shortest path as an observed packet path.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def collect(evidence_path):
    raw = Path(evidence_path).read_bytes()
    evidence = json.loads(raw)
    profiles, cases = {}, {}
    for case in evidence['cases']:
        ref = case['source_files']['topology']
        path = Path(ref['path'])
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != ref['sha256']:
            raise ValueError('Topology source hash mismatch: '+case['id'])
        topology = json.loads(content)
        counts = Counter(n['kind'] for n in topology['nodes'])
        if dict(counts) != case['topology_summary']['counts']:
            raise ValueError('Topology/source summary mismatch')
        profile = ref['sha256']
        profiles[profile] = topology
        config = case['documents']['input']
        groups = config['parallel']['groups']
        if sorted(r for g in groups for r in g) != list(range(counts['gpu'])):
            raise ValueError('EP groups do not partition the physical GPUs')
        cases[case['id']] = dict(profile=profile, groups=groups,
                                workload=config['workload'])
    return dict(schema_version='runtime-network-layouts/1',
                evidence_sha256=hashlib.sha256(raw).hexdigest(),
                profiles=profiles, cases=cases,
                path_semantics='computed_reachable_shortest_path_not_observed_ECMP',
                simulation_executed=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = collect(a.evidence)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, separators=(',', ':'))
        stream.write('\n')
    print(json.dumps(dict(cases=len(result['cases']), profiles=len(result['profiles']))))


if __name__ == '__main__':
    main()
