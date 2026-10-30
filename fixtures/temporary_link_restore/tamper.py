"""Confirm raw-record checks reject corrupt notification, byte and timing state."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from fixtures.temporary_link_restore.audit import audit, load


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    t, w = (load(a.stage/'input'/n) for n in ('topology/topology.json', 'work.json'))
    source = {n: load(a.stage/'network'/n/'result.json') for n in ('stale-down', 'physical-before-report', 'inflight')}
    mutations = []
    r = deepcopy(source['stale-down'])
    next(x for x in r['link_failures']['events'] if x['kind'] == 'routes_update_ignored')['known_revision'] = 1
    mutations.append(('stale-down', 'old report accepted over new known version', r))
    r = deepcopy(source['stale-down'])
    r['link_failures']['route_epoch'] += 1
    mutations.append(('stale-down', 'ignored report changed route epoch', r))
    r = deepcopy(source['physical-before-report'])
    next(x for x in r['link_failures']['events'] if x.get('notification_revision') == 1)['known_state'] = 'up'
    mutations.append(('physical-before-report', 'physical truth substituted for known down state', r))
    r = deepcopy(source['inflight'])
    next(x for x in r['link_failures']['events'] if x['kind'] == 'link_up')['time_ns'] += 1
    mutations.append(('inflight', 'wrong actual recovery time', r))
    r = deepcopy(source['stale-down'])
    r['persistent_transport']['work'][0]['acked_ns'] -= 1
    mutations.append(('stale-down', 'source ACK synthesized before real feedback', r))
    r = deepcopy(source['stale-down'])
    r['mmu_snapshot']['switches'][0]['shared_used_bytes'] = 1
    mutations.append(('stale-down', 'residual switch buffer hidden at exit', r))
    results = []
    for name, change, r in mutations:
        c = load(a.stage/'input'/(name+'.json'))
        try:
            audit(t, c, w, r)
        except AssertionError:
            results.append(dict(condition=name, mutation=change, rejected=True))
        else:
            raise AssertionError('Accepted corrupt record: '+change)
    with a.output.open('x') as s:
        json.dump(dict(passed=True, mutations=results), s, indent=2)
        s.write('\n')
    print(json.dumps(dict(passed=True, rejected=len(results))))


if __name__ == '__main__':
    main()
