"""Run six fixed public native data microbenchmarks and one deterministic repeat."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--observed', type=Path, required=True)
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); observed, backend, out = (v.resolve() for v in (a.observed, a.backend, a.output))
    assert not out.exists() and backend.is_file() and os.access(backend, os.X_OK)
    index = json.loads((observed / 'index.json').read_text()); cfg = index['config']
    for name, info in index['artifacts'].items(): assert sha(observed / name) == info['sha256']
    out.mkdir(); commands = []
    def cli(label, args):
        cmd = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', *map(str, args)]
        row = dict(label=label, command=cmd, cwd=str(ROOT)); commands.append(row); start = time.monotonic()
        try:
            with (out / (label + '.stdout.log')).open('w') as stdout, (out / (label + '.stderr.log')).open('w') as stderr:
                child = subprocess.run(cmd, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=60)
            row['exit_code'] = child.returncode
            assert child.returncode == 0, 'Public CLI failed; preserve outputs and stop'
        finally:
            row['wall_seconds'] = time.monotonic() - start; save(out / 'commands.json', commands)
    for network in cfg['network_profiles']:
        cli('topology-' + network, ['topology', '--config', observed / ('topology-' + network + '.json'), '--output', out / ('topology-' + network)])
        for condition in cfg['placement_conditions']:
            label = network + '-' + condition
            cli(label, ['rdma-stream-probe', '--topology', out / ('topology-' + network), '--protocol', observed / 'protocol.json',
                '--config', observed / ('work-' + condition + '.json'), '--backend', backend, '--output', out / label])
    network, condition = cfg['repeat_network'], cfg['repeat_condition']
    cli('repeat', ['rdma-stream-probe', '--topology', out / ('topology-' + network), '--protocol', observed / 'protocol.json',
        '--config', observed / ('work-' + condition + '.json'), '--backend', backend, '--output', out / 'repeat'])
    assert (out / 'repeat/result.json').read_bytes() == (out / (network + '-' + condition) / 'result.json').read_bytes()
    save(out / 'execution.json', dict(commands=len(commands), completed=True, data_only=True,
        backend=str(backend), backend_sha256=sha(backend), observed_index_sha256=sha(observed / 'index.json'),
        platform_base_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, timeout=10).strip(),
        network_executed=True, gpu_executed=False, modern_GIN_executed=False, hardware_calibrated=False, goal_complete=False))
    print(json.dumps(dict(completed=True, conditions=6, repeats=1, public_commands=len(commands))))


if __name__ == '__main__': main()
