"""Require Python and direct native whole-schedule validation to reject bad input."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_link_failures import validate_topology


def load(p):
    return json.loads(p.read_bytes())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('stage', 'build', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    inp = a.stage/'input'
    topology = load(inp/'topology/topology.json')
    base = load(inp/'ordered-local.json')
    cases = []
    for key, value in [('restore_time_ns', True), ('restore_time_ns', 1.5), ('restore_time_ns', -1),
                       ('restore_time_ns', 4000), ('restore_time_ns', 3999), ('restore_time_ns', 5000000),
                       ('restore_route_update_delay_ns', True), ('restore_route_update_delay_ns', -1),
                       ('restore_route_update_delay_ns', 4976000), ('extra', 0)]:
        c = deepcopy(base)
        c['link_failures']['events'][0][key] = value
        cases.append(c)
    for missing in ('restore_time_ns', 'restore_route_update_delay_ns'):
        c = deepcopy(base)
        del c['link_failures']['events'][0][missing]
        cases.append(c)
    c = deepcopy(base)
    c['link_failures']['mode'] = 'switch_links/1'
    cases.append(c)
    c = deepcopy(base)
    c['link_failures']['events'] *= 2
    cases.append(c)
    c = deepcopy(base)
    c['link_failures']['events'][0].update(src=0, dst=128)
    cases.append(c)
    c = deepcopy(base)
    c['link_failures']['events'].append(dict(c['link_failures']['events'][0], src=128, dst=129))
    cases.append(c)
    backend = load(a.build/'backend.json')['stream_probe']
    rows = []
    for i, c in enumerate(cases):
        try:
            validate_topology(topology, c)
        except ValueError:
            pass
        else:
            raise AssertionError('Python accepted invalid schedule '+str(i))
        path = a.output/(str(i)+'.json')
        path.write_text(json.dumps(c, indent=2)+'\n')
        result = a.output/(str(i)+'-result.json')
        command = [backend, str(inp/'topology/topology.json'), str(path), str(inp/'work.json'), str(result)]
        r = subprocess.run(command, capture_output=True, text=True)
        (a.output/(str(i)+'.stdout.log')).write_text(r.stdout)
        (a.output/(str(i)+'.stderr.log')).write_text(r.stderr)
        assert r.returncode != 0 and not result.exists() and 'RDMA:' in r.stderr, (i, r.stderr)
        rows.append(dict(case=i, command=command, exit_code=r.returncode, no_result=True))
    (a.output/'verification.json').write_text(json.dumps(dict(passed=True, cases=rows), indent=2)+'\n')
    print(json.dumps(dict(passed=True, invalid_schedules=len(rows), network_allocation_not_reached=True)))


if __name__ == '__main__':
    main()
