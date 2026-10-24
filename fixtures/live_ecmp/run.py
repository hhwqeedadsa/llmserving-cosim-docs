"""Five primary functional networks; fresh source workflow owns slot six."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def normalized_default(old, new, libraries):
    values = deepcopy([old, new])
    for value in values:
        seen = set()
        for i, name in enumerate(value['loaded_libraries']):
            resolved = str(Path(name).resolve())
            if resolved in libraries:
                assert sha(Path(name)) == libraries[resolved]
                value['loaded_libraries'][i] = resolved; seen.add(resolved)
        assert seen == set(libraries)
    assert values[0] == values[1], 'dynamic default result changed beyond verified library path spellings'
    return dict(raw_result_byte_equal=old == new, canonical_result_equal=True,
                excluded_scope='only spelling of eight hash-verified identical native library paths')


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'stage'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); s = a.stage.resolve()
    assert not (s/'network-commands.json').exists(); build = load(s/'build.json')
    assert sha(s/'bin/stream-probe') == build['binaries']['stream-probe']
    source = repo/'examples/scaleout_live_ecmp'; inputs = s/'inputs'; inputs.mkdir()
    for path in source.glob('*.json'): (inputs/path.name).write_bytes(path.read_bytes())
    default = load(inputs/'protocol.json'); del default['local_failover']; save(inputs/'default.json', default)
    overlap = load(inputs/'protocol.json')
    overlap['link_failures']['events'].append(dict(src=12, dst=14, time_ns=5000, route_update_delay_ns=1000))
    save(inputs/'overlap.json', overlap)
    zero = load(inputs/'protocol.json'); zero['link_failures']['events'][0].update(time_ns=0, route_update_delay_ns=0); save(inputs/'zero.json', zero)
    (s/'network').mkdir(); commands = []
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    def run(label, argv, network=False):
        assert sum(v['network'] for v in commands) < 5 or not network
        assert sum(load(p)['wall_seconds'] for p in s.rglob('probe.json'))+70 < 720
        cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(s/(label+'-monitor')),
            '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31), '--artifact-bytes', str(128*2**20),
            '--artifact-root', str(s), '--min-free-bytes', str(8*2**30), '--', *map(str, argv)]
        commands.append(dict(label=label, command=cmd, cwd=str(repo), network=network, status='started')); save(s/'network-commands.json', commands)
        done = subprocess.run(cmd, cwd=repo, timeout=65); commands[-1].update(exit_code=done.returncode, status='terminal'); save(s/'network-commands.json', commands)
        assert done.returncode == 0, label+': stop, no retry'
    run('topology', [*cli, 'topology', '--config', inputs/'topology.json', '--output', s/'topology'])
    from moe_ub_cosim.scaleout_streams import validate
    for filename in ('default.json', 'protocol.json', 'overlap.json', 'zero.json'):
        validate(load(s/'topology/topology.json'), load(inputs/filename), load(inputs/'work.json'))
    for name, proto, backend in (('default-old', 'default.json', Path('/dev/shm/moe-persistent-scale-20261024/bin/stream-probe')),
            ('default-new', 'default.json', s/'bin/stream-probe'), ('live', 'protocol.json', s/'bin/stream-probe'),
            ('overlap', 'overlap.json', s/'bin/stream-probe'), ('zero', 'zero.json', s/'bin/stream-probe')):
        run(name, [*cli, 'rdma-stream-probe', '--topology', s/'topology', '--protocol', inputs/proto,
            '--config', inputs/'work.json', '--backend', backend, '--output', s/'network'/name], True)
    comparison = normalized_default(load(s/'network/default-old/result.json'), load(s/'network/default-new/result.json'), build['libraries'])
    save(s/'run.json', dict(passed=True, actual_networks=5, sixth_reserved_for_fresh=True,
        default_comparison=comparison, inputs_directory=inputs.name))


if __name__ == '__main__': main()
