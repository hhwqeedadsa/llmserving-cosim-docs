"""Build and run an allocation-only probe at a pinned Alibaba ns-3 commit; no download."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
COMMIT = '3e0c7c1bfbbe9f77890ddcf5e5b9c79fc6dd7437'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--build-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True, help='New directory in runs/')
    p.add_argument('--cmake', default='cmake')
    p.add_argument('--ninja', default='ninja')
    args = p.parse_args()
    source, build, output = (x.resolve() for x in (args.source, args.build_dir, args.output))
    for path, parent in ((source, 'data'), (build, 'build'), (output, 'runs')):
        if path == ROOT / parent or not path.is_relative_to(ROOT / parent):
            raise ValueError('Use a dedicated directory in ' + parent)
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != COMMIT:
        raise ValueError('Use the pinned independent research clone; no automatic checkout')
    if Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=source, text=True).strip()).resolve() != source:
        raise ValueError('Source must be an independent clone')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=source).returncode:
        raise ValueError('Tracked source changed; preserve it and use a clean clone')
    cmake, ninja = shutil.which(args.cmake), shutil.which(args.ninja)
    if not cmake or not ninja:
        raise ValueError('Pass available cmake and ninja executables')
    probe = HERE / 'probes/switch_memory.cc'
    target = source / 'simulation/scratch/scaleout-switch-memory.cc'
    if target.exists() and target.read_bytes() != probe.read_bytes():
        raise ValueError('Scratch probe differs; do not overwrite it')
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(probe, target)
    sources = [source / 'simulation/src/point-to-point/model' / name for name in
               ('switch-node.h', 'switch-node.cc', 'nvswitch-node.h', 'nvswitch-node.cc', 'switch-mmu.h')]
    provenance = dict(commit=COMMIT, source=str(source),
                      files={str(f.relative_to(source)): hashlib.sha256(f.read_bytes()).hexdigest() for f in sources},
                      probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(), commands=[])

    def step(name, command, seconds, small=False):
        argv = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output / name),
                '--rss-bytes', str(536870912 if small else 2147483648),
                '--address-bytes', str(1073741824 if small else 4294967296),
                '--artifact-bytes', '536870912', '--wall-seconds', str(seconds),
                '--artifact-root', str(source), '--artifact-root', str(build),
                '--artifact-root', str(output), '--min-free-bytes', '8589934592', '--', *command]
        provenance['commands'].append(dict(cwd=str(ROOT), argv=argv))
        (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
        subprocess.run(argv, cwd=ROOT, check=True, timeout=seconds + 30)

    step('configure', [cmake, '-S', str(source / 'simulation'), '-B', str(build), '-G', 'Ninja',
         '-DCMAKE_MAKE_PROGRAM=' + ninja, '-DNS3_ENABLED_MODULES=core;network;internet;point-to-point;applications',
         '-DNS3_OUTPUT_DIRECTORY=' + str(build / 'output'), '-DCMAKE_BUILD_TYPE=Release',
         '-DNS3_WARNINGS_AS_ERRORS=OFF', '-DNS3_PRECOMPILE_HEADERS=OFF', '-DNS3_EXAMPLES=OFF',
         '-DNS3_TESTS=OFF', '-DNS3_MPI=OFF', '-DNS3_MTP=OFF', '-DNS3_PYTHON_BINDINGS=OFF'], 120)
    step('build', [cmake, '--build', str(build), '--target', 'scratch_scaleout-switch-memory', '--parallel', '2'], 300)
    results = dict(scope='Allocation-only probe, not a network or thousand-GPU run', source_commit=COMMIT)
    for count in (0, 8):
        name = 'count-' + str(count)
        step(name, [str(build / 'output/scratch/ns3.36.1-scaleout-switch-memory'), '--count=' + str(count)], 15, True)
        results[name] = json.loads((output / name / 'stdout.log').read_text())
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
