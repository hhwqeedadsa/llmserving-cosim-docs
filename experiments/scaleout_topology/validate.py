"""Bounded public-CLI topology generation; no native build or network execution."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    deadline = time.monotonic() + 300
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True, help='New dedicated directory under runs/')
    args = p.parse_args()
    output = args.output.resolve()
    if output == ROOT / 'runs' or not output.is_relative_to(ROOT / 'runs'):
        raise ValueError('Output must be a new dedicated directory under runs/')
    output.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, scope='Static generation and independent functional checks; no native network execution',
                  platform_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  worktree_status=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True),
                  validation_sha256=sha(Path(__file__)), commands=[], examples={})
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')

    def step(label, command):
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            raise ValueError('Aggregate 300s validation budget exhausted')
        argv = [sys.executable, '-m', 'moe_ub_cosim.host_probe', '--output-dir', str(output / label),
                '--rss-bytes', '1073741824', '--address-bytes', '2147483648',
                '--artifact-bytes', '134217728', '--wall-seconds', str(remaining),
                '--artifact-root', str(output), '--', *command]
        report['commands'].append(dict(cwd=str(ROOT), argv=argv))
        (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
        subprocess.run(argv, cwd=ROOT, check=True, timeout=remaining + 5)
        return json.loads((output / label / 'probe.json').read_text())

    report['tests'] = step('tests', [sys.executable, '-m', 'unittest', 'discover',
                                   '-s', 'tests/unit', '-p', 'scaleout*_test.py', '-v'])
    for config in sorted((ROOT / 'examples/scaleout_topology').glob('*.json')):
        dirs = []
        for repeat in (1, 2):
            label = config.stem + '-' + str(repeat)
            dest = output / (label + '-generated')
            step(label, [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', 'topology',
                         '--config', str(config), '--output', str(dest)])
            dirs.append(dest)
        if {f.name: sha(f) for f in dirs[0].iterdir()} != {f.name: sha(f) for f in dirs[1].iterdir()}:
            raise ValueError('Repeated generation differed: ' + config.stem)
        audit = json.loads((dirs[0] / 'audit.json').read_text())
        report['examples'][config.stem] = dict(
            input_sha256=sha(config), manifest_sha256=sha(dirs[0] / 'manifest.json'),
            counts=audit['counts'], link_tiers=audit['link_tiers'],
            all_gpu_pairs_have_packet_switch_path=audit['all_gpu_pairs_have_packet_switch_path'],
            unreachable_unordered_gpu_pairs=audit['unreachable_unordered_gpu_pairs'],
            fixed_switch_counter_array_bytes=audit['fixed_switch_counter_array_bytes'],
            repeated_files_byte_identical=True, network_executed=False, warnings=audit['warnings'])
    free = shutil.disk_usage(output).free
    report['native_loader'] = dict(status='not_run', free_disk_bytes=free, required_reserve_bytes=8589934592,
                                   reason='below_native_probe_disk_reserve' if free < 8589934592
                                   else 'not_part_of_static_validation')
    report['passed'] = True
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=True, output=str(output), examples=report['examples'],
                          native_loader=report['native_loader']), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
