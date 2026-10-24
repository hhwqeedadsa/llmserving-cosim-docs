"""Incremental consumer build; dependencies and SR carrier object are read only."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for key in ('repository', 'stage', 'native_include', 'native_lib', 'fabric_archive', 'persistent_object'):
        p.add_argument('--'+key.replace('_', '-'), type=Path, required=True)
    p.add_argument('--revision',default='')
    a = p.parse_args(); repo=a.repository.resolve(); stage=a.stage.resolve()
    objects=stage/'objects'; objects.mkdir(); binary=stage/'bin'; binary.mkdir()
    libs=sorted(a.native_lib.glob('libns3*.so')); assert len(libs)==8
    identity={str(p.resolve()):sha(p) for p in [a.fabric_archive, a.persistent_object, *libs]}
    def run(label, command):
        subprocess.run([sys.executable, '-B', '-m', 'moe_ub_cosim.host_probe',
            '--output-dir', str(stage/(a.revision+label+'-monitor')), '--wall-seconds', '60',
            '--rss-bytes', str(2**30), '--address-bytes', str(2**31),
            '--artifact-bytes', str(64*2**20), '--artifact-root', str(objects), '--artifact-root', str(binary),
            '--min-free-bytes', str(8*2**30), '--', *map(str, command)], cwd=repo, check=True, timeout=65)
    flags=['c++', '-std=c++20', '-O0', '-DNDEBUG', '-DNS3_BUILD_PROFILE_RELEASE', '-D__LINUX__',
        '-Wall', '-Wextra', '-I'+str(a.native_include), '-I'+str(repo/'backend/rdma'),
        '-I'+str(repo/'third_party/LLMServingSim/astra-sim/extern/helper')]
    for name in ('remote_transactions', 'transaction_probe', 'transaction_session'):
        run('compile-'+name, [*flags, '-c', repo/'backend/rdma'/(name+'.cc'), '-o', objects/(name+'.o')])
    for name in ('probe', 'session'):
        run('link-'+name, ['c++', objects/('transaction_'+name+'.o'), objects/'remote_transactions.o',
            a.persistent_object, a.fabric_archive, '-Wl,--disable-new-dtags',
            '-Wl,-rpath,$ORIGIN/../lib', '-Wl,--no-as-needed', *libs, '-o', binary/('transaction-'+name)])
    for name in ('remote_memory', 'transaction_limits'):
        run('compile-'+name+'-oracle', ['c++', '-std=c++20', '-O0', '-I'+str(repo/'backend'),
            repo/'tests/unit'/(name+'_test.cc'), '-o', binary/(name+'-oracle')])
        run(name+'-oracle', [binary/(name+'-oracle')])
    run('python-tests', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests/unit', '-p', '*transaction*test.py'])
    for name, digest in identity.items():
        assert sha(Path(name))==digest
    (stage/(a.revision+'build.json')).write_text(json.dumps(dict(inputs=identity,
        binaries={p.name:sha(p) for p in binary.iterdir()},
        scope='incremental consumers plus CPU checks; no clean dependency build'), indent=2)+'\n')


if __name__ == '__main__':
    main()
