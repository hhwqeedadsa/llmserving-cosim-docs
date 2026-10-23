"""Execute the predeclared fixed-workload packet-loss conditions via public CLI."""
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


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text())
def save(p, v): Path(p).write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--runtime-manifest', type=Path, required=True)
    p.add_argument('--library-manifest', type=Path, required=True)
    p.add_argument('--protoc', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    out = a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free >= 8*2**30 + 192*2**20
    runtime = read(a.runtime_manifest)
    expected = runtime['binaries']['ns3.42-moe-rdma-service']['sha256']
    assert a.backend.is_file() and os.access(a.backend, os.X_OK) and digest(a.backend) == expected
    relevant = ['backend/rdma/'+name for name in ('fabric.hh', 'fabric.cc', 'astra_adapter.hh',
                'astra_adapter.cc', 'service.cc', 'logging.cc', 'recovery.hh', 'recovery.cc', 'recovery_records.hh')]
    matching = {}
    for path in relevant:
        if path in runtime['source_hashes']:
            assert digest(ROOT/path) == runtime['source_hashes'][path], 'service source changed: '+path
            matching[path] = digest(ROOT/path)
    libraries = {p: sha for p, sha in read(a.library_manifest).items() if p.endswith('.so')}
    assert len(libraries) == 8
    assert all(digest(p) == sha for p, sha in libraries.items())
    assert a.protoc.is_file() and os.access(a.protoc, os.X_OK)
    out.mkdir()
    for name in ('service.json', 'topology.json', 'protocol.json'):
        shutil.copyfile(HERE/name, out/name)
    started = time.monotonic(); commands = []; cases = []; target = None
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT/'python'))

    def cli(label, args):
        assert time.monotonic()-started < 300
        size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file())
        assert size < 192*2**20 and shutil.disk_usage(out).free >= 8*2**30
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]
        before = time.monotonic()
        with (out/(label+'.stdout')).open('w') as stdout, (out/(label+'.stderr')).open('w') as stderr:
            r = subprocess.run(command, cwd=ROOT, env=environment, stdout=stdout, stderr=stderr,
                               timeout=min(60, 300-(time.monotonic()-started)), check=False)
        commands.append(dict(label=label, command=command, cwd=str(ROOT), exit_code=r.returncode,
                             wall_seconds=time.monotonic()-before))
        save(out/'commands.json', commands)
        assert r.returncode == 0, 'unexpected failure; no retry: '+label

    definitions = [('native-clean', None, 'clean'), ('native-middle', None, 'middle')]
    for rto in (40000, 20000, 12000):
        for fault in (('clean', 'middle', 'tail', 'ack') if rto != 12000 else ('clean', 'tail', 'ack')):
            definitions.append((f'r{rto//1000}-{fault}', rto, fault))
    assert len(definitions) == 13
    save(out/'design.json', dict(cases=[dict(name=n, rto_ns=r, fault=f) for n, r, f in definitions],
                                 scope='predeclared deterministic interventions, not independent stochastic trials'))
    cli('topology', ['topology', '--config', out/'topology.json', '--output', out/'fabric'])
    base = read(HERE/'protocol.json')
    for name, rto, fault in definitions:
        protocol = deepcopy(base)
        if rto:
            protocol['recovery'] = dict(mode='go_back_n', initial_rto_ns=rto,
                                        max_rto_ns=160000, max_retries=3, max_events=30000)
        if fault != 'clean':
            assert target
            payload = protocol['payload_bytes']; total = target['bytes']
            sequence = (total//2//payload)*payload if fault == 'middle' else ((total-1)//payload)*payload
            if fault == 'ack': sequence = total
            protocol['faults'] = [dict(node=target['src'] if fault == 'ack' else target['dst'], port=0,
                message_id=target['id'], kind='ack' if fault == 'ack' else 'data', sequence=sequence,
                count=1, start_ns=0, end_ns=5000000)]
        save(out/(name+'.json'), protocol)
        cli(name, ['rdma-run', '--topology', out/'fabric', '--protocol', out/(name+'.json'),
                   '--config', out/'service.json', '--backend', a.backend, '--protoc', a.protoc, '--output', out/name])
        result = read(out/name/'result.json')
        assert result['passed'] and result['packet_capture']['complete']
        if target is None:
            wave = read(out/name/'graphs/g0.w0/wave.json')
            choices = [m for m in wave['messages'] if m['phase'] == 'dispatch' and m['src'] == 0 and m['dst'] == 4]
            assert len(choices) == 1
            target = choices[0]; save(out/'target.json', target)
        if fault != 'clean': assert result['fault_injection']['drop_counts'] == [1]
        graphs = {}
        for path in sorted((out/name/'graphs').glob('*/wave.json')):
            assert path.read_bytes() == (out/'native-clean/graphs'/path.parent.name/'wave.json').read_bytes()
            graphs[path.parent.name] = digest(path)
        cases.append(dict(name=name, rto_ns=rto, fault=fault, graphs=graphs, result_sha256=digest(out/name/'result.json')))
        save(out/'progress.json', dict(state='running', cases=cases))
        cli(name+'-diagnosis', ['rdma-diagnose', '--run', out/name, '--output', out/(name+'-diagnosis.json')])
    assert all(digest(p) == sha for p, sha in libraries.items()) and digest(a.backend) == expected
    save(out/'experiment.json', dict(state='completed', cases=cases, target=target,
         platform_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
         runtime=dict(backend=str(a.backend), sha256=expected, libraries=libraries, matching_build_sources=matching),
         inputs={p.name: digest(p) for p in HERE.iterdir() if p.is_file()},
         wall_seconds=time.monotonic()-started, hardware_calibrated=False,
         network='one shared native RDMA simulation per condition; no UB, GPU or modern GIN'))
    save(out/'progress.json', dict(state='completed', cases=cases))
    print(json.dumps(dict(passed=True, cases=len(cases), target=target, wall_seconds=time.monotonic()-started)))


if __name__ == '__main__': main()
