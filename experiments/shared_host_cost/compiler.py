"""Read existing build evidence; never configure, compile or run a network."""
import argparse
from collections import Counter
from pathlib import Path
import shlex
import shutil
from experiments.shared_uplink_choice.prepare import load, save, sha

NATIVE = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'


def inspect(directory):
    commands = load(directory/'compile_commands.json')
    counts = Counter()
    important = []
    for row in commands:
        argv = row.get('arguments') or shlex.split(row['command'])
        flags = [s for s in argv if s.startswith('-O')]
        assert flags, 'missing optimization flags'
        counts[tuple(flags)] += 1
        assert not any('MOE_VERIFY_PROGRESS_UPDATES' in s for s in argv), 'development oracle enabled'
        if Path(row['file']).name in ('transaction_session.cc', 'remote_transactions.cc',
                'persistent_streams.cc', 'fabric.cc', 'simulator.cc', 'default-simulator-impl.cc',
                'qbb-net-device.cc', 'switch-node.cc', 'rdma-hw.cc'):
            important.append(dict(source=Path(row['file']).name, flags=flags, effective=flags[-1]))
    cache = (directory/'CMakeCache.txt').read_text()
    assert 'CMAKE_BUILD_TYPE:STRING=Release\n' in cache and 'NS3_WARNINGS:BOOL=ON\n' in cache
    source = (directory/'macros-and-definitions.cmake').read_text()
    assert 'if(${NS3_WARNINGS})' in source and 'add_compile_options(-Wall -O0)' in source
    assert len(commands) == 437 and counts == {('-O3', '-O0'): 437}
    return dict(translation_units=len(commands), optimization_sequences=[dict(flags=list(k), units=v)
        for k, v in sorted(counts.items())], effective_optimization='-O0', release_label_is_optimized=False,
        important_units=important, progress_full_scan_development_oracle=False,
        cause='NS3_WARNINGS=ON adds -Wall -O0 after the Release flags',
        source=NATIVE+'/build-support/macros-and-definitions.cmake', source_line=231,
        optimized_backend_performance_measured=False)


def capture(repo, stage):
    directory = stage/'compiler-evidence'
    directory.mkdir()
    build = repo/'runs/physical_nic_binding_20261027/build'
    inputs = {'compile_commands.json': build/'cmake/compile_commands.json',
        'CMakeCache.txt': build/'cmake/CMakeCache.txt', 'provenance.json': build/'provenance.json',
        'macros-and-definitions.cmake': repo/NATIVE/'build-support/macros-and-definitions.cmake'}
    for name, path in inputs.items():
        shutil.copyfile(path, directory/name)
    identity = load(stage/'runtime-identity.json')
    provenance = load(directory/'provenance.json')
    assert provenance['native_commit'] == identity['native_commit'] and provenance['sources'] == identity['sources']
    links = {}
    for name, digest in identity['files'].items():
        if name == 'run-transaction-session': continue
        original = build/'output'/('moe-rdma/ns3.42-moe-rdma-transaction-session' if name == 'transaction-session' else name)
        assert sha(original) == digest == sha(stage/'runtime'/name), 'different build bytes'
        links[name] = digest
    save(directory/'identity.json', dict(files={n: sha(directory/n) for n in inputs},
        runtime_build_bytes=links, provenance_matches_runtime_identity=True,
        new_builds=0, new_networks=0))
    save(directory/'summary.json', inspect(directory))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    capture(a.repository.resolve(), a.stage.resolve())
