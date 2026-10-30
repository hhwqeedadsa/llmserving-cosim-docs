"""Reject changed source identities and malformed saved dependency probes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from audit import closure, load, notification_probes, require, summarize, verify_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rejected = []

    def reject(label, action):
        try:
            action()
        except ValueError:
            rejected.append(label)
        else:
            raise AssertionError('accepted corruption: ' + label)

    evidence = load(args.audit/'sources.json')
    source = evidence[0]
    raw = Path(source['local_path']).read_bytes()
    verify_source(raw, source, source['commit'])
    for field, replacement in [('commit', '0'*40), ('sha256', '0'*64),
                               ('git_blob_sha', '0'*40), ('bytes', len(raw)+1),
                               ('public_url', 'https://example.invalid/unpinned')]:
        changed = dict(source, **{field: replacement})
        reject('source-'+field, lambda: verify_source(raw, changed, source['commit']))
    reject('same-size-source-byte-change', lambda: verify_source(b'X'+raw[1:], source, source['commit']))
    plan = load(args.audit/'plans/s0-l0/plan.json')
    require(len(closure(plan))==len(plan['events']), 'unmodified plan not reachable')
    changed = deepcopy(plan)
    changed['events'].append(changed['events'][0])
    reject('duplicate-event', lambda: closure(changed))
    changed = deepcopy(plan)
    changed['events'][-1]['deps'].append('not-an-event')
    reject('unknown-dependency', lambda: closure(changed))
    changed = deepcopy(plan)
    changed['events'][-1]['deps'] *= 2
    reject('duplicate-dependency', lambda: closure(changed))
    reject('unknown-held-event', lambda: closure(plan, ['not-an-event']))
    changed = deepcopy(plan)
    changed['events'] = [r for r in changed['events'] if r['id']!='m0.combine.r31.complete']
    reject('missing-required-completion', lambda: summarize(changed, set()))
    actual = load(args.audit/'analysis.json')
    require(actual['notification_probes']==notification_probes(), 'notification examples changed')
    require(len(actual['comparisons'])==8 and len(actual['input_projection'])==4, 'incomplete registered probes')
    require(actual['historical_network_unchanged'] and not actual['network_executed'], 'scope changed')
    result = dict(passed=True, rejected_cases=rejected, rejection_count=len(rejected),
                  complete_registered_probes=8, new_networks=0)
    with args.output.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
