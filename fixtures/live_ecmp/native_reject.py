"""Native malformed policies must fail before network allocation/output."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def main():
    s = Path(sys.argv[1]).resolve(); inputs = s/'native-reject-inputs'; inputs.mkdir()
    work_inputs = s/json.loads((s/'run.json').read_text())['inputs_directory']
    base = json.loads((work_inputs/'protocol.json').read_text()); cases = []
    for i, policy in enumerate((None, False, [], {}, dict(mode=None), dict(mode=True), dict(mode='bad'), dict(mode='live_ecmp/1', extra=0))):
        p = deepcopy(base); p['local_failover'] = policy; cases.append((str(i), p))
    p = deepcopy(base); del p['link_failures']; cases.append(('no-schedule', p)); records = []
    for name, protocol in cases:
        config = inputs/(name+'.json'); config.write_text(json.dumps(protocol)+'\n'); output = inputs/(name+'-result.json')
        cmd = [str(s/'bin/stream-probe'), str(s/'topology/topology.json'), str(config), str(work_inputs/'work.json'), str(output)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        (inputs/(name+'.stderr.log')).write_text(r.stderr)
        assert r.returncode != 0 and 'local failover requires' in r.stderr and not output.exists()
        records.append(dict(name=name, command=cmd, exit_code=r.returncode, output_created=False))
    (s/'native-reject.json').write_text(json.dumps(dict(passed=True, rejected_before_allocation=9, commands=records), indent=2)+'\n')


if __name__ == '__main__': main()
