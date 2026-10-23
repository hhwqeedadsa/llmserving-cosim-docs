"""Predeclared feedback-delay interventions through public platform CLI only."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
def read(p): return json.loads(Path(p).read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('backend', 'runtime-manifest', 'library-manifest', 'protoc', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--resume', action='store_true', help='Reuse verified completed conditions after an external interruption; never rerun a started network')
    p.add_argument('--bootstrap-baseline', type=Path, help='Completed primary run; execute only the preregistered nine Karn80 controls within its cumulative budget')
    a = p.parse_args(); out = a.output.resolve(); started = time.monotonic()
    assert out.exists() == a.resume and shutil.disk_usage(out.parent).free > 8*2**30+256*2**20
    runtime = read(a.runtime_manifest); binary_sha = runtime['binaries']['ns3.42-moe-rdma-service']['sha256']
    assert sha(a.backend) == binary_sha and os.access(a.backend, os.X_OK)
    relevant = ['backend/rdma/'+name for name in ('fabric.hh', 'fabric.cc', 'astra_adapter.hh', 'astra_adapter.cc',
                 'service.cc', 'logging.cc', 'recovery.hh', 'recovery.cc', 'recovery_records.hh')]
    sources = {path: sha(ROOT/path) for path in relevant}
    assert all(sources[path] == runtime['source_hashes'][path] for path in sources)
    libs = {name: h for name, h in read(a.library_manifest).items() if name.endswith('.so')}
    assert len(libs) == 8 and all(sha(path) == h for path, h in libs.items())
    assert a.protoc.is_file() and os.access(a.protoc, os.X_OK)
    if not a.resume: out.mkdir()
    cases = read(out/'progress.json')['cases'] if a.resume else []
    commands = read(out/'commands.json') if a.resume else []
    target = read(out/'target.json') if a.resume else None
    prior_seconds = sum(c['wall_seconds'] for c in commands)
    if a.bootstrap_baseline:
        assert not a.resume
        baseline = read(a.bootstrap_baseline/'experiment.json')
        assert baseline['state'] == 'completed' and len(baseline['cases']) == 45
        assert baseline['runtime']['sha256'] == binary_sha and baseline['runtime']['sources'] == sources
        prior_seconds = baseline['wall_seconds']
    environment = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    definitions = [dict(name=f'd{d}-{policy}-{fault}', delay_ns=d, policy=policy, fault=fault)
                   for d in (500, 2000, 4000) for policy in (('karn80',) if a.bootstrap_baseline else ('fixed80', 'fixed20', 'fixed40', 'karn40', 'history40'))
                   for fault in ('clean', 'tail', 'ack')]
    assert len(definitions) == (9 if a.bootstrap_baseline else 45)
    if a.resume:
        assert not (out/'experiment.json').exists() and read(out/'design.json') == definitions
        assert [c['name'] for c in cases] == [c['name'] for c in definitions[:len(cases)]]
        assert all(sha(out/c['name']/'result.json') == c['result_sha256'] for c in cases)
        for name in ('service.json', 'protocol.json', 'topology.json'): assert sha(HERE/name) == sha(out/name)
    else:
        save(out/'design.json', definitions)
        for name in ('service.json', 'protocol.json', 'topology.json'): shutil.copyfile(HERE/name, out/name)
    def cli(label, args):
        elapsed = prior_seconds+time.monotonic()-started
        assert elapsed < 300 and sum(p.stat().st_size for p in out.rglob('*') if p.is_file()) < 256*2**20
        assert shutil.disk_usage(out).free > 8*2**30
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]
        before = time.monotonic()
        with (out/(label+'.stdout')).open('w') as stdout, (out/(label+'.stderr')).open('w') as stderr:
            r = subprocess.run(command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                               timeout=min(60, 300-elapsed), check=False)
        commands.append(dict(command=command, cwd=str(ROOT), exit_code=r.returncode, wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands)
        assert r.returncode == 0, 'preserved unexpected failure, no retry: '+label
    for delay in (500, 2000, 4000):
        topology = read(HERE/'topology.json')
        for link in topology['links'].values(): link['delay_ns'] = delay
        if a.resume:
            assert read(out/f'topology-{delay}.json') == topology and (out/f'fabric-{delay}/topology.json').is_file()
        else:
            save(out/f'topology-{delay}.json', topology)
            cli(f'topology-{delay}', ['topology', '--config', out/f'topology-{delay}.json', '--output', out/f'fabric-{delay}'])
    reference_graphs = cases[0]['graphs'] if cases else None
    for case in definitions:
        name, delay, policy, fault = (case[k] for k in ('name', 'delay_ns', 'policy', 'fault'))
        if name in {c['name'] for c in cases}: continue
        if (out/name).exists():
            # Only a controller interruption BEFORE any native execution can be resumed.
            # Never erase or rerun a real network prefix or a failed service monitor.
            allowed = {'provenance.json', 'rdma.json', 'topology.json', 'input.json'}
            assert {p.name for p in (out/name).iterdir()} <= allowed and (out/name/'provenance.json').is_file()
            assert read(out/name/'provenance.json')['hashes'][str(a.backend)] == binary_sha
            preserved = out/'pre-network-interruption'; preserved.mkdir(exist_ok=True)
            (out/name).rename(preserved/name)
            for suffix in ('.stdout', '.stderr'):
                if (out/(name+suffix)).exists(): (out/(name+suffix)).rename(preserved/(name+suffix))
            save(preserved/'reason.json', dict(case=name, reason='external controller interruption; no execution directory/result/monitor or live original process', native_network_reexecuted=False))
        config = read(HERE/'protocol.json')
        recovery = dict(mode='go_back_n', initial_rto_ns=int(policy.removeprefix('fixed'))*1000 if policy.startswith('fixed') else 80000 if policy=='karn80' else 40000,
                        max_rto_ns=640000, max_retries=3, max_events=100000)
        if policy in ('karn40', 'karn80', 'history40'):
            recovery['rtt_estimator'] = dict(kind='karn', min_rto_ns=20000, granularity_ns=1)
        if policy == 'history40': recovery['history'] = dict(max_entries_per_source=16, max_age_ns=1000000)
        config['recovery'] = recovery
        if fault != 'clean':
            assert target
            config['faults'] = [dict(node=target['src'] if fault == 'ack' else target['dst'], port=0,
                message_id=target['id'], kind='ack' if fault == 'ack' else 'data',
                sequence=target['bytes'] if fault == 'ack' else 6000, count=1, start_ns=0, end_ns=5000000)]
        save(out/(name+'.json'), config)
        cli(name, ['rdma-run', '--topology', out/f'fabric-{delay}', '--protocol', out/(name+'.json'),
                   '--config', out/'service.json', '--backend', a.backend, '--protoc', a.protoc, '--output', out/name])
        result = read(out/name/'result.json')
        assert result['passed'] and result['drained'] and result['packet_capture']['complete']
        if target is None:
            wave = read(out/name/'graphs/g0.w0/wave.json')
            choices = [m for m in wave['messages'] if m['src'] == 0 and m['dst'] == 4 and m['phase'] == 'dispatch']
            assert len(choices) == 1 and choices[0]['bytes'] == 6144
            target = choices[0]; save(out/'target.json', target)
        if fault != 'clean': assert result['fault_injection']['drop_counts'] == [1]
        graphs = {p.parent.name: sha(p) for p in (out/name/'graphs').glob('*/wave.json')}
        if reference_graphs is None: reference_graphs = graphs
        assert graphs == reference_graphs
        assert len(result['messages']) == 92 and sum(m['bytes'] for m in result['messages']) == 389120
        cases.append(dict(case, result_sha256=sha(out/name/'result.json'), graphs=graphs))
        save(out/'progress.json', dict(state='running', cases=cases))
        cli(name+'-diagnose', ['rdma-diagnose', '--run', out/name, '--output', out/(name+'-diagnosis.json')])
    assert all(sha(path) == h for path, h in libs.items()) and sha(a.backend) == binary_sha
    if a.bootstrap_baseline:
        assert all(c['graphs'] == baseline['cases'][0]['graphs'] for c in cases)
    save(out/'experiment.json', dict(state='completed', cases=cases, target=target,
        platform_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        runtime=dict(binary=str(a.backend), sha256=binary_sha, sources=sources, libraries=libs),
        input_hashes={p.name: sha(p) for p in HERE.iterdir() if p.is_file()}, wall_seconds=prior_seconds+time.monotonic()-started,
        hardware_calibrated=False, modern_GIN=False, independent_stochastic_repetitions=0))
    save(out/'progress.json', dict(state='completed', cases=cases)); print(json.dumps(dict(passed=True, cases=len(cases))))

if __name__ == '__main__': main()
