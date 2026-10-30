"""Run the documented public topology/repair preparation from independent inputs."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    stage = a.stage.resolve()
    work = stage/'public'
    work.mkdir(exist_ok=False)
    commands = [
        [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'topology', '--config',
         str(ROOT/'examples/scaleout_repair_plan/clos-128.json'), '--output', str(work/'topology')],
        [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'repair-plan', '--topology',
         str(work/'topology'), '--src', '128', '--dst', '132', '--output', str(work/'repair')],
    ]
    receipts = []
    for index, command in enumerate(commands):
        with (work/(str(index)+'.stdout.log')).open('x') as out, (work/(str(index)+'.stderr.log')).open('x') as err:
            r = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err)
        receipts.append(dict(command=command, cwd=str(ROOT), exit_code=r.returncode))
        (work/'commands.json').write_text(json.dumps(receipts, indent=2)+'\n')
        if r.returncode:
            raise SystemExit(r.returncode)
    result = json.loads((work/'repair/summary.json').read_bytes())
    assert not result['network_executed'] and result['gpu_endpoints'] == 128
    print(json.dumps(result))


if __name__ == '__main__':
    main()
