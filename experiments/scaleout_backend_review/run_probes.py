"""Reproduce bounded non-UB probes against the reviewed Alibaba source, without downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def executable(value):
    result = shutil.which(value)
    if result is None:
        raise ValueError(f'Executable unavailable: {value}; pass its explicit path')
    return str(Path(result).resolve())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True, help='Research git clone inside this repository data/ or downloads/')
    p.add_argument('--build-dir', type=Path, required=True, help='Dedicated ignored build directory; may reuse this probe build')
    p.add_argument('--output', type=Path, required=True, help='New run directory; never overwrite')
    p.add_argument('--cmake', default='cmake')
    p.add_argument('--ninja', default='ninja')
    args = p.parse_args()
    source, build, output = (x.resolve() for x in (args.source, args.build_dir, args.output))
    for target, allowed in [(source, ('data', 'downloads')), (build, ('build',)), (output, ('runs',))]:
        if not any(target.is_relative_to(ROOT / name) and target != ROOT / name for name in allowed):
            raise ValueError(f'Use a dedicated directory inside {allowed}: {target}')
    if len({source, build, output}) != 3 or any(a.is_relative_to(b) for a in (source, build, output) for b in (source, build, output) if a != b):
        raise ValueError('Source, build and output must be distinct, non-nested directories')
    cmake, ninja = executable(args.cmake), executable(args.ninja)
    expected = next(s for s in json.loads((HERE / 'sources.json').read_text())['sources'] if s['name'] == 'ns-3-alibabacloud')
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=source, text=True).strip()
    if actual != expected['commit'] or Path(top).resolve() != source:
        raise ValueError('Source must be an independent clone at the reviewed commit')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=source).returncode != 0:
        raise ValueError('Preserve tracked source modifications; use a clean research clone')
    for entry in expected['files']:
        if hashlib.sha256((source / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Reviewed source changed: ' + entry['path'])
    copies = [('time_units.cc', 'scaleout-time-units.cc'), ('rdma_callbacks.cc', 'scaleout-rdma-callbacks.cc')]
    for local, name in copies:
        target = source / 'simulation/scratch' / name
        if target.exists() and target.read_bytes() != (HERE / local).read_bytes():
            raise ValueError('Existing scratch differs; preserve it and use a separate clone: ' + str(target))
    if output.exists():
        raise ValueError('Output exists: ' + str(output))
    output.mkdir(parents=True)
    for local, name in copies:
        shutil.copyfile(HERE / local, source / 'simulation/scratch' / name)
    commands = []

    def step(name, command, seconds, small=False):
        argv = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output / name),
                '--rss-bytes', str(268435456 if small else 2147483648),
                '--address-bytes', str(536870912 if small else 4294967296),
                '--artifact-bytes', '536870912', '--wall-seconds', str(seconds),
                '--artifact-root', str(source), '--artifact-root', str(build),
                '--artifact-root', str(output), '--min-free-bytes', '8589934592', '--', *command]
        commands.append(dict(name=name, argv=argv, cwd=str(ROOT)))
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        subprocess.run(argv, cwd=ROOT, check=True, timeout=seconds+30)

    step('configure', [cmake, '-S', str(source / 'simulation'), '-B', str(build), '-G', 'Ninja',
         '-DCMAKE_MAKE_PROGRAM=' + ninja, '-DNS3_ENABLED_MODULES=core;network;internet;point-to-point;applications',
         '-DNS3_OUTPUT_DIRECTORY=' + str(build / 'output'), '-DCMAKE_BUILD_TYPE=Release',
         '-DNS3_WARNINGS_AS_ERRORS=OFF', '-DNS3_PRECOMPILE_HEADERS=OFF', '-DNS3_EXAMPLES=OFF',
         '-DNS3_TESTS=OFF', '-DNS3_MPI=OFF', '-DNS3_MTP=OFF', '-DNS3_PYTHON_BINDINGS=OFF'], 120)
    step('build', [cmake, '--build', str(build), '--target', 'scratch_scaleout-time-units',
                   'scratch_scaleout-rdma-callbacks', '--parallel', '2'], 300)
    results = dict(source_commit=actual, scope='Time units and direct-link RDMA only; no fabric or service validation')
    for name in ('time-units', 'rdma-callbacks'):
        step(name, [str(build / 'output/scratch' / ('ns3.36.1-scaleout-' + name))], 15, small=True)
        results[name] = json.loads((output / name / 'stdout.log').read_text())
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
