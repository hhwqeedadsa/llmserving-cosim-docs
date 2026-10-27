"""Independent public build acceptance; no research code or performance claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from moe_ub_cosim.host_probe import probe


def load(p): return json.loads(p.read_bytes())
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def run(stage, label, command, *, build=False, network=False):
    path = stage/'commands.json'; records = load(path) if path.exists() else []
    assert label not in {r['label'] for r in records}, 'do not retry a recorded command'
    assert all(r.get('exit_code') == 0 for r in records), 'stop after failure'
    seconds = sum(load(p).get('wall_seconds', 0) for p in stage.rglob('probe.json'))
    assert seconds+(1030 if build else 65) < 2400, 'cumulative monitored seconds'
    assert sum(p.stat().st_size for p in stage.rglob('*') if p.is_file())+2**29 < 2**31
    assert sum(r['network'] for r in records)+int(network) <= 2
    row = dict(label=label, command=list(map(str, command)), cwd=str(Path.cwd()),
        network=network, exit_code=None)
    records.append(row); save(path, records)
    result = probe(row['command'], stage/'monitor'/label, rss_bytes=2**31 if build else 2**30,
        address_bytes=2**32 if build else 2**31, wall_seconds=1000 if build else 60,
        artifact_bytes=2**31-2**21, artifact_roots=[stage], min_free_bytes=8*2**30)
    row.update(exit_code=result['child_exit_code'], status=result['status']); save(path, records)
    assert row['exit_code'] == 0 and row['status'] == 'completed', 'preserve failure and stop'
    print(label, 'passed', flush=True)


def prepare(repo, stage):
    from fixtures.physical_nic_binding.checkpoint import admit
    archive = repo/'runs/physical_nic_binding_20261027/checkpoint.tar.gz'
    digest = '4cff1ee79454faca83ecc1aa732c4d0927f6ff4e6498fbb518f405cf519e2b27'
    blobs, manifest = admit(archive, digest)
    inputs = stage/'inputs'; inputs.mkdir(); copied = {}
    for name, raw in blobs.items():
        for old, new in (('evidence/modern/', 'reference-modern/'),
                         ('evidence/shared-topology/', 'modern-topology/')):
            if name.startswith(old):
                relative = new+name.removeprefix(old)
                dest = stage/relative; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(raw)
                copied[relative] = manifest['files'][name]['sha256']
    for old, new in (('evidence/inputs/modern-input.json', 'modern-input.json'),
                     ('evidence/inputs/protocol.json', 'modern-protocol.json')):
        (inputs/new).write_bytes(blobs[old]); copied['inputs/'+new] = manifest['files'][old]['sha256']
    for old, new in (('small-clos-100g.json', 'probe-topology.json'), ('messages.json', 'probe-messages.json')):
        shutil.copyfile(repo/'examples/scaleout_rdma'/old, inputs/new)
    protocol = load(repo/'examples/scaleout_rdma/rdma.json')
    protocol.update(queue_metrics=True, packet_capture=dict(nodes=[], start_ns=0,
        end_ns=5000000, max_events=20000, wire_headers=True))
    save(inputs/'probe-protocol.json', protocol)
    for p in inputs.iterdir(): copied[str(p.relative_to(stage))] = sha(p)
    save(stage/'reference-identity.json', dict(archive_sha256=digest, files=copied,
        original_libraries={Path(n).name: r['sha256'] for n, r in manifest['files'].items()
            if n.startswith('evidence/build/output/lib/')}))


def main():
    p = argparse.ArgumentParser(); p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True); p.add_argument('--prepare-only', action='store_true')
    a = p.parse_args(); repo, stage = a.repository.resolve(), a.stage.resolve()
    if a.prepare_only: prepare(repo, stage); return
    stage.mkdir(); (stage/'empty-cwd').mkdir(); (stage/'monitor').mkdir()
    os.environ['PYTHONPATH'] = str(repo/'python')+':'+str(repo)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.chdir(stage/'empty-cwd')
    cli = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout']
    run(stage, 'unit-tests', [sys.executable, '-B', '-m', 'unittest', 'tests.unit.native_compilation_test',
        'tests.unit.native_identity_test', 'tests.unit.scaleout_rdma_test'])
    run(stage, 'prepare', [sys.executable, '-B', '-m', 'fixtures.native_release_build.workflow',
        '--repository', repo, '--stage', stage, '--prepare-only'])
    run(stage, 'public-help', [*cli, 'rdma-build', '--help'])
    run(stage, 'fresh-build', [*cli, 'rdma-build', '--output', stage/'build',
        '--cmake', '/home/lry/tools/venv/bin/cmake', '--ninja', '/home/lry/tools/venv/bin/ninja',
        '--protobuf-prefix', '/home/lry/tools/protobuf', '--compile-wall-seconds', '900'], build=True)
    backend = load(stage/'build/backend.json')
    run(stage, 'probe-topology', [*cli, 'topology', '--config', stage/'inputs/probe-topology.json',
        '--output', stage/'probe-topology'])
    run(stage, 'probe-network', [*cli, 'rdma-probe', '--topology', stage/'probe-topology',
        '--config', stage/'inputs/probe-protocol.json', '--messages', stage/'inputs/probe-messages.json',
        '--backend', backend['probe'], '--output', stage/'probe'], network=True)
    run(stage, 'modern-network', [*cli, 'modern-deepep-native', '--topology', stage/'modern-topology',
        '--protocol', stage/'inputs/modern-protocol.json', '--config', stage/'inputs/modern-input.json',
        '--backend', backend['transaction_session'], '--output', stage/'modern', '--callback-batches'], network=True)
    save(stage/'execution.json', dict(state='completed_networks_pending_independent_acceptance',
        network_executions=2, network_reruns=0, compile_attempts=1, hardware_calibrated=False,
        wall_speedup_measured=False, downloads=0, GPU_executions=0))


if __name__ == '__main__': main()
