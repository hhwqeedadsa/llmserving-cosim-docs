"""Bounded incremental build; immutable native libraries and old runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, x): p.write_text(json.dumps(x, indent=2)+'\n')


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'stage', 'native-include', 'baseline'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); repo = a.repository.resolve(); s = a.stage.resolve(); old = a.baseline.resolve()
    assert not (s/'build-commands.json').exists()
    objects = s/'objects'; objects.mkdir(); binary = s/'bin'; binary.mkdir()
    baseline = json.loads((old/'build.json').read_text()); libs = sorted((old/'lib').glob('libns3*.so'))
    archive = Path('/dev/shm/moe-switch-link-failure-20261024/objects/libobserved-fabric.a')
    identity = {str(p): sha(p) for p in [archive, old/'bin/stream-probe', *libs]}
    assert len(libs) == 8 and all(baseline['inputs'][str(p)] == sha(p) for p in [archive, *libs])
    assert sha(old/'bin/stream-probe') == baseline['binaries']['stream-probe']
    commands = []
    def run(label, command, cwd=repo):
        total = sum(json.loads(p.read_text())['wall_seconds'] for p in s.rglob('probe.json'))
        assert total+70 < 720
        cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(s/(label+'-monitor')),
            '--wall-seconds', '60', '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
            '--artifact-bytes', str(128*2**20), '--artifact-root', str(s), '--min-free-bytes', str(8*2**30), '--', *map(str, command)]
        # host_probe needs the public package even when ar runs in objects/.
        import os
        env = dict(os.environ, PYTHONPATH=str(repo/'python'), PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run(cmd, cwd=cwd, env=env, timeout=65)
        commands.append(dict(label=label, command=cmd, cwd=str(cwd), exit_code=done.returncode)); save(s/'build-commands.json', commands)
        assert done.returncode == 0, label+': no build retry'
    run('extract-fixed-objects', ['ar', 'x', archive, 'astra_adapter.cc.o', 'recovery.cc.o'], objects)
    flags = ['c++', '-std=c++20', '-O0', '-DNDEBUG', '-DNS3_BUILD_PROFILE_RELEASE', '-D__LINUX__', '-Wall', '-Wextra',
        '-I'+str(a.native_include), '-I'+str(repo/'backend/rdma'), '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('fabric', 'persistent_streams', 'remote_transactions', 'stream_probe', 'transaction_probe', 'transaction_session'):
        run('compile-'+name, [*flags, '-c', repo/'backend/rdma'/(name+'.cc'), '-o', objects/(name+'.o')])
    run('archive', ['ar', 'rcs', objects/'liblive-fabric.a', objects/'fabric.o', objects/'astra_adapter.cc.o', objects/'recovery.cc.o'])
    for name in ('stream-probe', 'transaction-probe', 'transaction-session'):
        objs = [objects/(name.replace('-', '_')+'.o')]
        if name.startswith('transaction'): objs.append(objects/'remote_transactions.o')
        run('link-'+name, ['c++', *objs, objects/'persistent_streams.o', objects/'liblive-fabric.a',
            '-Wl,--disable-new-dtags', '-Wl,-rpath,'+str(old/'lib'), '-Wl,--no-as-needed', *libs, '-o', binary/name])
    assert all(sha(Path(p)) == h for p, h in identity.items())
    save(s/'build.json', dict(inputs=identity, binaries={p.name: sha(p) for p in binary.iterdir()},
        sources={str(p.relative_to(repo)): sha(p) for p in (repo/'backend/rdma').rglob('*') if p.suffix in ('.cc', '.hh')},
        libraries={str(p): sha(p) for p in libs}, scope='incremental Fabric and header consumers; fixed native libraries and old auxiliary objects; no clean dependency build'))


if __name__ == '__main__': main()
