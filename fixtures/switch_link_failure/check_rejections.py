"""Run malformed schedules through Python and the native pre-allocation guard."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess

from moe_ub_cosim.scaleout_rdma import validate_probe


def main(topology, config, messages, backend, output):
    t = json.loads(topology.read_text())
    c = json.loads(config.read_text())
    m = json.loads(messages.read_text())
    variants = {}
    for name, key, value in [('boolean_src', 'src', True), ('fractional_dst', 'dst', 10.5),
                             ('negative_src', 'src', -1), ('too_large_dst', 'dst', 1280),
                             ('same_endpoint', 'dst', 8), ('negative_time', 'time_ns', -1),
                             ('boolean_time', 'time_ns', False), ('cutoff', 'time_ns', 5000000),
                             ('negative_delay', 'route_update_delay_ns', -1),
                             ('late_update', 'route_update_delay_ns', 4996000),
                             ('unknown_field', 'extra', 0)]:
        variant = deepcopy(c)
        variant['link_failures']['events'][0][key] = value
        variants[name] = variant
    for name, pair in [('nic_edge', (0, 8)), ('non_edge', (8, 9)), ('missing_node', (8, 1279))]:
        variant = deepcopy(c)
        variant['link_failures']['events'][0].update(src=pair[0], dst=pair[1])
        variants[name] = variant
    for name, value in [('empty', []), ('duplicate', c['link_failures']['events']*2),
                         ('too_many', c['link_failures']['events']*9)]:
        variant = deepcopy(c)
        variant['link_failures']['events'] = value
        variants[name] = variant
    variant = deepcopy(c)
    variant['link_failures']['events'].append(dict(src=0, dst=8, time_ns=5000, route_update_delay_ns=0))
    variants['valid_then_invalid'] = variant
    output.mkdir(exist_ok=False)
    records = {}
    for name, variant in variants.items():
        try:
            validate_probe(t, variant, m)
        except ValueError as error:
            rejection = str(error)
        else:
            raise ValueError('Python accepted '+name)
        path = output/(name+'.json')
        path.write_text(json.dumps(variant, indent=2)+'\n')
        result = output/(name+'-result.json')
        cmd = [str(backend), str(topology), str(path), str(messages), str(result)]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        if run.returncode != 1 or result.exists() or not run.stderr.strip():
            raise ValueError('native guard did not reject before result '+name)
        records[name] = dict(command=cmd, cwd=str(Path.cwd()), python_rejection=rejection,
                             native_exit=run.returncode, stdout=run.stdout, stderr=run.stderr,
                             native_result_created=False)
    (output/'rejections.json').write_text(json.dumps(records, indent=2, sort_keys=True)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('topology', 'config', 'messages', 'backend', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    main(args.topology, args.config, args.messages, args.backend, args.output)
