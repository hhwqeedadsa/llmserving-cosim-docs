"""Run two cross-Pod EP4 replicas in one persistent fabric through public CLI.

The first-packet path and overlapping message intervals prove shared resource
use. They do not quantify the delay caused exclusively by inter-group contention.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend-json', type=Path, required=True)
    parser.add_argument('--protoc', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    (out / 'logs').mkdir()
    commands = []

    def cli(name, arguments):
        command = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str, arguments)]
        with (out / 'logs' / (name + '.stdout')).open('w') as stdout, \
             (out / 'logs' / (name + '.stderr')).open('w') as stderr:
            result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=90)
        commands.append(dict(argv=command, cwd=str(ROOT), exit_code=result.returncode))
        save(out / 'commands.json', commands)
        if result.returncode:
            raise RuntimeError('shared-fabric check failed; inspect ' + name + ' logs')

    examples = ROOT / 'examples/scaleout_rdma'
    config = read(examples / 'service.json')
    config['parallel']['groups'] = [[0, 2, 4, 6], [1, 3, 5, 7]]
    save(out / 'service.json', config)
    cli('topology', ['topology', '--config', examples / 'small-clos-100g.json', '--output', out / 'topology'])
    cli('service', ['rdma-run', '--config', out / 'service.json', '--topology', out / 'topology',
                   '--protocol', examples / 'rdma.json', '--backend', read(args.backend_json)['service'],
                   '--protoc', args.protoc, '--output', out / 'service'])
    cli('independent-check', ['rdma-check', '--run', out / 'service'])
    run = out / 'service'
    result, frontend, topology = (read(run / f) for f in ('result.json', 'frontend.json', 'topology.json'))
    assert result['passed'] and result['service']['active_graphs'] == 0
    first = [w for w in frontend['history'] if w['step'] == 0]
    assert len(first) == 2 and all(w['start_ns'] == 0 for w in first)
    # These records come from one result and one native process, not two runs.
    membership = {}
    for wavefile in sorted((run / 'graphs').glob('*/wave.json')):
        wave = read(wavefile)
        for message in wave['messages']:
            membership[message['id']] = wave['group_index']
    ports = {(p['node'], p['port']): p for p in result['ports']}
    nodes = {n['id']: n for n in topology['nodes']}
    by_port = defaultdict(list)
    for message in result['messages']:
        for hop in message['first_packet_path']:
            key = hop['node'], hop['port']
            if ports[key]['tier'] == 'agg_core' and nodes[hop['node']]['kind'] == 'agg':
                by_port[key].append(message)
    shared = []
    for key, messages in sorted(by_port.items()):
        groups = {membership[m['id']] for m in messages}
        if len(groups) < 2:
            continue
        overlaps = []
        for left in messages:
            for right in messages:
                if membership[left['id']] >= membership[right['id']]:
                    continue
                start = max(left['first_tx_ns'], right['first_tx_ns'])
                end = min(left['receive_ready_ns'], right['receive_ready_ns'])
                if start < end:
                    overlaps.append(dict(messages=[left['id'], right['id']], interval_ns=[start, end]))
        shared.append(dict(node=key[0], port=key[1], group_ids=sorted(groups),
                           messages=len(messages), overlapping_interval_examples=overlaps[:3],
                           max_queue_bytes=ports[key]['max_queue_at_hook_bytes']))
    assert shared and any(p['overlapping_interval_examples'] for p in shared)
    save(out / 'summary.json', dict(passed=True, ep_groups=config['parallel']['groups'],
         finish_ns=result['finish_ns'], verification=read(run / 'verification.json'),
         first_wave_start_ns=[w['start_ns'] for w in first], shared_agg_egress_ports=shared,
         interpretation='One native process/fabric; both groups use the same physical Agg egresses. '
                        'Overlapping message lifetimes are not an additive contention-delay estimate.'))


if __name__ == '__main__':
    main()
