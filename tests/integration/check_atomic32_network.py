"""Run each declared 128-endpoint functional case once using the public CLI.

The separate audit reads retained packet bytes and memory events. This driver
never imports another workflow or a historical experiment's expected output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True,
                        help='Fresh rdma-build backend.json')
    parser.add_argument('--case', choices=('normal', 'recovery', 'session-recovery'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = args.inputs.resolve(); output = args.output.resolve()
    backend = json.loads(args.backend.read_bytes())
    session = args.case == 'session-recovery'
    executable = Path(backend['transaction_session' if session else 'transaction_probe'])
    protocol = inputs / ('normal.json' if args.case == 'normal' else 'recovery.json')
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout',
               'rdma-transaction-session' if session else 'rdma-transaction-probe',
               '--topology', str(inputs/'topology'), '--protocol', str(protocol),
               '--config', str(inputs/'work.json'), '--backend', str(executable),
               '--output', str(output/'run')]
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    files = [inputs/'work.json', protocol, inputs/'topology/topology.json',
             args.backend.resolve(), executable, Path(__file__).resolve()]
    receipt = dict(case=args.case, argv=command, cwd=str(output),
                   sha256={str(p): digest(p) for p in files})
    (output/'command.json').write_text(json.dumps(receipt, indent=2)+'\n')
    start = time.monotonic()
    with (output/'stdout.log').open('x') as stdout, (output/'stderr.log').open('x') as stderr:
        result = subprocess.run(command, cwd=output, env=env, stdout=stdout,
                                stderr=stderr, timeout=100)
    receipt.update(exit_code=result.returncode, wall_seconds=time.monotonic()-start)
    (output/'command.json').write_text(json.dumps(receipt, indent=2)+'\n')
    if result.returncode:
        raise RuntimeError('Public command failed; retained logs, no automatic retry')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
