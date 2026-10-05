"""Reproducible wrappers for the already verified native build boundary."""
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .plan import integer, require
from .user_io import ROOT, save, timestamp


def doctor(backend=None):
    tools = {name: shutil.which(name) for name in ('git', 'cmake', 'ninja', 'c++', 'protoc')}
    packages = {}
    for name in ('protobuf', 'numpy', 'pandas', 'rich', 'PyYAML'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    dependency = subprocess.run([sys.executable, str(ROOT/'scripts/check_deps.py')],
        cwd=ROOT, capture_output=True, text=True)
    backend_ok = backend is None or (backend.is_file() and os.access(backend, os.X_OK))
    memory = {}
    if Path('/proc/meminfo').is_file():
        for row in Path('/proc/meminfo').read_text().splitlines():
            key, value = row.split(':', 1)
            if key in ('MemTotal', 'MemAvailable', 'SwapTotal'):
                memory[key+'_bytes'] = int(value.split()[0])*1024
    return dict(passed=all(tools.values()) and all(packages.values()) and dependency.returncode == 0 and backend_ok,
        repository=str(ROOT), python=sys.version.split()[0], tools=tools, frontend_packages=packages,
        dependencies=dict(exit_code=dependency.returncode, output=dependency.stdout+dependency.stderr),
        backend=None if backend is None else dict(path=str(backend.resolve()), executable=backend_ok),
        available_disk_bytes=shutil.disk_usage(ROOT).free, memory=memory,
        checks_scope='Read-only presence/version checks; native headers/libraries are verified by build, not this command',
        missing_frontend_hint='Install the frontend extra in a virtual environment; offline slice help does not need it')


def build(directory, jobs):
    integer(jobs, 'jobs', 1, 64)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    logs = directory/'user-build-logs'; logs.mkdir(exist_ok=True)
    attempt = len(list(logs.glob('attempt-*')))+1
    log = logs/f'attempt-{attempt:04d}'; log.mkdir(exist_ok=False)
    commands = []

    def run(argv):
        ordinal = len(commands)
        item = dict(argv=[str(a) for a in argv], cwd=str(ROOT), started_utc=timestamp(),
                    log=str(log/f'{ordinal:02d}.log'))
        with Path(item['log']).open('x') as stream:
            p = subprocess.run(item['argv'], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
        item.update(exit_code=p.returncode, finished_utc=timestamp())
        commands.append(item); save(log/'commands.json', commands)
        require(p.returncode == 0, f'Build step failed ({p.returncode}); see {item["log"]}')

    run([sys.executable, ROOT/'scripts/check_deps.py'])
    schema = ROOT/'third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf'
    generated = [schema/f'et_def.pb.{ext}' for ext in ('h', 'cc')]
    # The locked ASTRA CMake consumes these ignored generated files. Do not
    # overwrite an existing generated source in another active build.
    if not all(p.is_file() for p in generated):
        require(not any(p.exists() for p in generated), 'Partial generated Chakra protobuf; inspect files before rebuilding')
        run(['protoc', '-I', schema, '--cpp_out='+str(schema), schema/'et_def.proto'])
    disabled = ('EXAMPLES', 'TESTS', 'MPI', 'MTP', 'ASSERT', 'LOG', 'WARNINGS_AS_ERRORS',
                'NATIVE_OPTIMIZATIONS', 'CCACHE', 'PRECOMPILE_HEADERS', 'FETCH_OPTIONAL_COMPONENTS',
                'NETANIM', 'CPM', 'VCPKG')
    configure = ['cmake', '-S', ROOT/'third_party/ns-3-ub', '-B', directory, '-G', 'Ninja',
                 '-DCMAKE_BUILD_TYPE=release', '-DCMAKE_PROJECT_NS3_INCLUDE='+str(ROOT/'backend/inject.cmake'),
                 '-DNS3_OUTPUT_DIRECTORY='+str(directory/'output'), '-DNS3_ENABLED_MODULES=unified-bus',
                 *('-DNS3_'+name+'=OFF' for name in disabled)]
    lock = ROOT/'third_party/ns-3-ub/.lock-ns3_linux_build'
    old_lock = lock.read_bytes() if lock.exists() else None
    try:
        run(configure)
        run(['cmake', '--build', directory, '--target', 'cosim-service', '-j', jobs])
    finally:
        if old_lock is not None:
            lock.write_bytes(old_lock)
        elif lock.exists():
            lock.unlink()
    binaries = list((directory/'output/cosim').glob('ns*-cosim-service'))
    require(len(binaries) == 1, 'Expected exactly one cosim-service executable')
    run(['ldd', binaries[0]])
    linked = Path(commands[-1]['log']).read_text()
    require('not found' not in linked, 'Missing runtime library; see build ldd log')
    libraries = [row.split('=>', 1)[1].split()[0] for row in linked.splitlines()
                 if 'libns3' in row and '=>' in row]
    require(libraries and all(Path(p).resolve().is_relative_to(directory/'output') for p in libraries),
            'Backend must link only this build of ns-3-UB')
    result = dict(passed=True, backend=str(binaries[0]), build_dir=str(directory),
                  linked_ns3_libraries=libraries, commands=str(log/'commands.json'))
    save(directory/'backend.json', result)
    return result
