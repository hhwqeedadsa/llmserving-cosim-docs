"""Check CLI and direct-native registration admission without executing a network."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('topology', 'protocol', 'work', 'backend', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    original = json.loads(args.work.read_text())
    cases = {}
    def change(name, edit):
        value = deepcopy(original);edit(value);cases[name] = value
    for field in ('registration_model', 'registrations'):
        change('missing-' + field, lambda w, f=field: w.pop(f))
    change('unknown-field', lambda w: w['registrations'][0].update(extra=0))
    change('duplicate-id', lambda w: w['registrations'][1].update(id=0))
    change('duplicate-scope', lambda w: w['registrations'][1].update(nic=1))
    change('unknown-nic', lambda w: w['registrations'][0].update(nic=99))
    change('unknown-storage', lambda w: w['registrations'][0].update(window_key=99))
    change('range', lambda w: w['registrations'][0].update(bytes=65))
    change('permission-escalation', lambda w: w['registrations'][3].update(allow_atomic=True))
    change('wrong-nic-key', lambda w: w['operations'][0].update(key=22))
    change('access-subrange', lambda w: w['operations'][1].update(address=8210))
    change('atomic-denied', lambda w: w['operations'][2].update(key=21))
    change('unordered-alias-write', lambda w: w['operations'][1].update(address=8192, after=[]))
    reports = []
    for name, value in cases.items():
        config = args.output / (name + '.json')
        config.write_text(json.dumps(value, indent=2) + '\n')
        for mode in ('cli', 'native'):
            target = args.output / (name + '-' + mode)
            if mode == 'cli':
                cmd = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'rdma-transaction-probe',
                       '--topology', str(args.topology), '--protocol', str(args.protocol),
                       '--config', str(config), '--backend', str(args.backend), '--output', str(target)]
            else:
                cmd = [str(args.backend), str(args.topology / 'topology.json'),
                       str(args.protocol), str(config), str(target)]
            result = subprocess.run(cmd, capture_output=True, timeout=10, env=os.environ)
            (args.output / (name + '-' + mode + '.stdout')).write_bytes(result.stdout)
            (args.output / (name + '-' + mode + '.stderr')).write_bytes(result.stderr)
            if result.returncode == 0 or target.exists() or not result.stderr:
                raise AssertionError('admission accepted/allocated output: ' + name + '/' + mode)
            reports.append(dict(case=name, mode=mode, command=cmd, exit_code=result.returncode,
                                result_or_run_directory_created=False))
    (args.output / 'verification.json').write_text(json.dumps(dict(
        schema_version='nic-registration-rejections/1', network_executed=False,
        conditions=len(cases), checks=reports), indent=2) + '\n')
    print(f'{len(cases)} invalid inputs rejected by both CLI and native')


if __name__ == '__main__':
    main()
