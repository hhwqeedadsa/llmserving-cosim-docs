"""Compare Python/native rejection before either native executable builds Fabric."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time

from moe_ub_cosim.scaleout_transactions import validate

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    inputs = args.inputs.resolve()
    work = json.loads((inputs/'work.json').read_bytes())
    topology = json.loads((inputs/'topology/topology.json').read_bytes())
    protocol = json.loads((inputs/'normal.json').read_bytes())
    backend = json.loads(args.backend.read_bytes()); commands = []; rejections = []
    changes = [
        ('atomic-value', ('operations', 1, 'value'), 2**32, 'session integer out of bounds'),
        ('atomic-alignment', ('operations', 1, 'address'), 8198, 'session atomic denied/unaligned'),
        ('atomic-range', ('operations', 1, 'address'), 8448, 'session memory range out of bounds'),
        ('atomic-permission', ('windows', 0, 'allow_atomic'), False, 'session atomic denied/unaligned'),
        ('wait-bits', ('operations', 6, 'local_wait', 0, 'bits'), 16, 'session wait bits must be 32 or 64'),
        ('wait-value', ('operations', 6, 'local_wait', 0, 'value'), 2**32, 'session integer out of bounds'),
        ('wait-reset', ('operations', 6, 'local_wait', 0, 'reset_to'), 2**32, 'session integer out of bounds'),
        ('wait-alignment', ('operations', 6, 'local_wait', 0, 'address'), 8198, 'invalid session wait comparison/address'),
        ('wait-overlap', ('operations', 6, 'local_wait', 1),
         dict(key=7, address=8192, compare='eq', value=0, bits=64), 'overlapping session wait ranges'),
    ]
    start = time.monotonic()
    for name, path, value, native_reason in changes:
        candidate = deepcopy(work); cursor = candidate
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        try:
            validate(topology, protocol, candidate)
        except ValueError as error:
            python_reason = str(error)
        else:
            raise AssertionError(name+' admitted by Python')
        file = out/(name+'.json'); file.write_text(json.dumps(candidate)+'\n')
        for executable_key in ('transaction_probe', 'transaction_session'):
            result_path = out/(name+'-'+executable_key+'-result.json')
            command = [backend[executable_key], str(inputs/'topology/topology.json'),
                       str(inputs/'normal.json'), str(file), str(result_path)]
            began = time.monotonic()
            result = subprocess.run(command, cwd=out, input='', capture_output=True,
                                    text=True, timeout=5, env=dict(os.environ))
            label = name+'-'+executable_key
            (out/(label+'.stdout')).write_text(result.stdout)
            (out/(label+'.stderr')).write_text(result.stderr)
            commands.append(dict(argv=command, cwd=str(out), exit_code=result.returncode,
                                 wall_seconds=time.monotonic()-began))
            (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
            assert result.returncode == 1 and native_reason in result.stderr, label
            assert not result.stdout and not result_path.exists(), label
        rejections.append(dict(case=name, python=python_reason, native=native_reason))
    summary = dict(passed=True, python_rejections=len(rejections), native_rejections=len(commands),
                   rejected_before_fabric=True, network_executed=False, rejections=rejections,
                   wall_seconds=time.monotonic()-start)
    (out/'verification.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
