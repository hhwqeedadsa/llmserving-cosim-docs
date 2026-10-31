"""Incremental transaction executables using an explicitly pinned native runtime."""
from pathlib import Path
import shutil
import subprocess
import time

from .native_identity import verify_native_source
from .scaleout_rdma import ROOT, checked_json, digest, monitored, save
from .scaleout_topology import require


RECOMPILED = {'backend/rdma/'+name for name in (
        'transaction_probe.cc', 'transaction_session.cc', 'remote_transactions.cc', 'persistent_streams.cc',
        'persistent_streams.hh', 'remote_memory.hh', 'remote_transactions.hh', 'transaction_limits.hh',
        'transaction_session_validation.hh', 'memory_registrations.hh', 'self_peer_service.hh')}


def _recorded_sources(old, expected):
    recorded = dict(old['sources'])
    # Existing incremental identities can refresh only the known adapters.
    # They cannot bless a changed Fabric/archive ABI without rebuilding it.
    adapters = RECOMPILED | {'backend/rdma/legacy_deepep_probe.cc', 'backend/rdma/legacy_execution_validation.hh'}
    for name in expected.get('recompiled_old_paths', []):
        require(name in adapters and name in expected['sources'], 'unsupported prebuilt adapter source: '+name)
        recorded[name] = expected['sources'][name]
    return recorded


def build(runtime, identity, output):
    runtime, identity, output = (Path(p).resolve() for p in (runtime, identity, output))
    require(not output.exists() and output.parent.is_dir(), 'build requires a new directory with existing parent')
    require(shutil.disk_usage(output.parent).free >= 8*2**30, 'build volume below 8GiB reserve')
    native = verify_native_source(ROOT)
    old = checked_json(runtime/'provenance.json', 2*2**20)
    expected = checked_json(identity, 2*2**20)
    require(old['native_commit'] == expected['native_commit'] == native, 'runtime identity differs from lock')
    # All units of both new executables are recompiled. No object from a prior
    # transaction build is reused after changing RemoteMemory/TransactionLimits.
    rebuilt = RECOMPILED
    recorded = _recorded_sources(old, expected)
    for name, wanted in recorded.items():
        if name not in rebuilt:
            require((ROOT/name).is_file() and digest(ROOT/name) == wanted, 'prebuilt source mismatch: '+name)
    hashes = expected['library_hashes']
    require(isinstance(hashes, dict) and len(hashes) == 8 and
            all(name.startswith('libns3.42-') and name.endswith('.so') and '/' not in name for name in hashes),
            'expected eight pinned native libraries')
    archive = runtime/'output/lib/libmoe-rdma-fabric.a'
    require(digest(archive) == expected['fabric_archive_sha256'], 'Fabric archive hash mismatch')
    members = subprocess.run(['ar', 't', str(archive)], check=True, capture_output=True, text=True, timeout=10).stdout.splitlines()
    require(sorted(members) == ['fabric.cc.o', 'recovery.cc.o', 'switch_drop_capture.cc.o'], 'unexpected Fabric archive members')
    libraries = [runtime/'output/lib'/name for name in hashes]
    require(all(p.is_file() and digest(p) == hashes[p.name] for p in libraries), 'native library hash mismatch')
    compiler = shutil.which('c++'); require(compiler, 'existing compiler required')
    sources = {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT/'backend/rdma').glob('*')) if p.is_file()}
    output.mkdir(); lib = output/'runtime/lib'; lib.mkdir(parents=True)
    for p in libraries+[archive]: shutil.copyfile(p, lib/p.name)
    common = [compiler, '-DNS3_BUILD_PROFILE_RELEASE', '-D__LINUX__', '-DNDEBUG', '-Wall', '-Wpedantic', '-O3',
              '-std=c++20', '-fPIE', '-fno-semantic-interposition', '-I'+str(runtime/'output/include'),
              '-I'+str(ROOT/'backend/rdma'), '-I'+str(ROOT/'third_party/LLMServingSim/astra-sim/extern/helper')]
    commands = []
    for name in ('persistent_streams', 'remote_transactions', 'transaction_probe', 'transaction_session'):
        commands.append([*common, '-c', str(ROOT/'backend/rdma'/(name+'.cc')), '-o', str(output/(name+'.o'))])
    binaries = {}
    for name in ('transaction_probe', 'transaction_session'):
        binary = output/('moe-rdma-'+name.replace('_', '-')); binaries[name] = str(binary)
        commands.append([compiler, *[str(output/(x+'.o')) for x in (name, 'remote_transactions', 'persistent_streams')],
                         str(lib/archive.name), '-Wl,--no-as-needed', *[str(lib/p.name) for p in libraries],
                         '-ldl', '-Wl,-rpath,$ORIGIN/runtime/lib', '-o', str(binary)])
    save(output/'build-inputs.json', dict(native_commit=native, runtime=str(runtime), identity_sha256=digest(identity),
        original_sources=recorded, sources=sources, recompiled_old_paths=sorted(rebuilt),
        fabric_archive_sha256=digest(archive), archive_members=members, library_hashes=hashes, commands=commands, ns3_builds=0))
    started = time.monotonic()
    for i, command in enumerate(commands):
        remaining = 900-(time.monotonic()-started)
        require(remaining > 0, 'incremental compilation budget exhausted')
        monitored(command, output/f'compile-{i}', [output], runtime=dict(wall_seconds=remaining,
            rss_bytes=3*2**30, address_bytes=6*2**30, artifact_bytes=512*2**20))
    for name, binary in binaries.items():
        linked = subprocess.run(['ldd', binary], check=True, capture_output=True, text=True, timeout=10).stdout
        (output/(name+'-ldd.txt')).write_text(linked); actual = {}
        for line in linked.splitlines():
            if 'libns3' in line and '=>' in line:
                soname, right = line.split('=>', 1); path = Path(right.split()[0]).resolve()
                require(path.parent == lib, 'transaction executable loaded another runtime')
                actual[soname.strip()] = digest(path)
        require(actual == hashes and 'not found' not in linked, 'loaded runtime differs from pinned hashes')
    result = dict(passed=True, **binaries, binary_hashes={k:digest(Path(v)) for k,v in binaries.items()},
        sources=sources, native_commit=native, library_hashes=hashes, ns3_builds=0,
        scope='four platform units compiled; pinned Fabric/native libraries copied and hash checked; no network run')
    save(output/'backend.json', result)
    return result
