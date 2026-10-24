"""Three bounded primary networks; fresh replay owns the fourth/last slot."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def load(p): return json.loads(p.read_text())
def save(p, x): p.write_text(json.dumps(x, indent=2, sort_keys=True)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for name in ('repository', 'stage', 'backend'):
        p.add_argument('--'+name, type=Path, required=True)
    a = p.parse_args(); repo, s, backend = a.repository.resolve(), a.stage.resolve(), a.backend.resolve()
    assert not (s/'commands.json').exists(), 'no automatic rerun/resume'
    corrected = load(s/'unit-corrected-monitor/probe.json')
    assert corrected['status'] == 'completed' and corrected['child_exit_code'] == 0
    reference = load(backend.parent.parent/'build.json')
    assert sha(backend) == reference['binaries']['stream-probe']
    libraries = {str(x): sha(x) for x in (backend.parent.parent/'lib').glob('libns3*.so')}
    assert len(libraries) == 8 and all(reference['inputs'][p] == h for p, h in libraries.items())
    inputs = s/'inputs'; inputs.mkdir()
    source = repo/'examples/scaleout_causal_waves'
    for f in source.glob('*.json'): shutil.copy2(f, inputs/f.name)
    topology = load(repo/'examples/scaleout_rdma/small-clos-100g.json')
    topology['deployment'].update(servers_per_segment=1, gpu_type='NONE')
    topology['fabric'].update(tors_per_segment_per_rail=1, aggs_per_pod_per_group=1, cores_per_group=1, tor_uplinks=1)
    save(inputs/'topology.json', topology)
    protocol = load(repo/'examples/scaleout_rdma/rdma.json'); protocol['schema_version'] = 'moe-scaleout-rdma/2'
    protocol['limits'] = dict(max_gpus=4, max_switches=16, max_links=128, max_messages=128,
        max_queue_events=1, queue_capture='summary', max_sim_ns=5000000, wall_seconds=60,
        rss_bytes=2**30, address_bytes=2**31, artifact_bytes=16*2**20, result_json_bytes=16*2**20)
    protocol['queue_metrics'] = True
    protocol['packet_capture'] = dict(nodes=[], start_ns=0, end_ns=5000000, max_events=100000, wire_headers=True)
    save(inputs/'normal-protocol.json', protocol)
    fault = deepcopy(protocol)
    fault['faults'] = [dict(node=2, port=1, message_id=0, kind='data', sequence=0, start_ns=0, end_ns=5000000, count=1)]
    save(inputs/'fault-protocol.json', fault)
    example = load(repo/'examples/scaleout_rdma_streams/work-selective.json')
    selective = load(inputs/'plan.json')
    for key in ('transport_recovery', 'dcqcn_reference'): selective[key] = example[key]
    save(inputs/'selective-plan.json', selective)
    save(s/'runtime.json', dict(backend=str(backend), sha256=sha(backend), libraries=libraries,
        native='6088174ad6928557482a968d45b9952c85e13112',
        source={str(p.relative_to(repo)): sha(p) for p in (repo/'python').rglob('*.py')},
        scope='existing verified native program, no build or bundled native runtime'))
    commands = []
    def run(label, command, network=False):
        monitors = [load(p) for p in s.rglob('probe.json')]
        assert sum(x['wall_seconds'] for x in monitors)+10 < 300
        assert sum(x.get('network', False) for x in commands) < 3 or not network
        cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(s/(label+'-monitor')),
            '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
            '--artifact-bytes', str(16*2**20), '--artifact-root', str(s/'inputs'), '--artifact-root', str(s/'topology'),
            '--artifact-root', str(s/'planned'), '--artifact-root', str(s/'network'), '--min-free-bytes', str(8*2**30), '--', *map(str, command)]
        done = subprocess.run(cmd, cwd=repo, timeout=65)
        commands.append(dict(label=label, command=cmd, cwd=str(repo), exit_code=done.returncode, network=network))
        save(s/'commands.json', commands); assert done.returncode == 0, label
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    (s/'planned').mkdir(); (s/'network').mkdir()
    run('topology', [*cli, 'topology', '--config', inputs/'topology.json', '--output', s/'topology'])
    for label, config, proto in (('plain', 'plan.json', 'normal-protocol.json'),
                                 ('selective', 'selective-plan.json', 'normal-protocol.json'),
                                 ('fault', 'selective-plan.json', 'fault-protocol.json')):
        run(label+'-plan', [*cli, 'rdma-causal-plan', '--config', inputs/config, '--topology', s/'topology',
            '--protocol', inputs/proto, '--output', s/'planned'/label])
        run(label+'-network', [*cli, 'rdma-stream-probe', '--topology', s/'topology', '--protocol', inputs/proto,
            '--config', s/'planned'/label/'work.json', '--backend', backend, '--output', s/'network'/label], True)
        assert (s/'network'/label/'result.json').stat().st_size <= 8*2**20
        run(label+'-audit', [sys.executable, '-B', repo/'fixtures/causal_waves/audit.py', '--config', inputs/config,
            '--work', s/'planned'/label/'work.json', '--mapping', s/'planned'/label/'mapping.json',
            '--result', s/'network'/label/'result.json', '--output', s/'network'/label/'audit.json'])
    assert sha(backend) == reference['binaries']['stream-probe']
    assert all(sha(Path(p)) == h for p, h in libraries.items())
    assert (s/'planned/selective/work.json').read_bytes() == (s/'planned/fault/work.json').read_bytes()
    save(s/'run.json', dict(passed=True, actual_networks=3, no_build=True, no_optimization_claim=True))


if __name__ == '__main__':
    main()
