"""Fresh public contribution callback workflow; no backend or network run."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def bound():
    resource.setrlimit(resource.RLIMIT_AS, (2**30, 2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(ROOT/'examples/contribution_attempts/input.json', out/'input.json')
    env = dict(os.environ, PYTHONPATH=str(ROOT/'python'), PYTHONDONTWRITEBYTECODE='1')
    commands = []

    def cli(name, config, target, expected=0):
        command = [sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'contribution-check',
                   '--config', config, '--output', target]
        with (out/(name+'.stdout')).open('w') as stdout, (out/(name+'.stderr')).open('w') as stderr:
            result = subprocess.run(command, cwd=out, env=env, timeout=10, preexec_fn=bound,
                                    stdout=stdout, stderr=stderr)
        commands.append(dict(command=command, cwd=str(out), exit_code=result.returncode, expected_exit_code=expected))
        (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert result.returncode == expected, name
        return json.loads((out/(target+'/report.json')).read_bytes()) if expected == 0 else None

    result = cli('full', 'input.json', 'full')
    assert result['transcript_valid'] and result['declared_call_retirable']
    assert result['accepted_experts'] == [40, 42, 80] and result['missing_experts'] == []
    assert result['logical_complete_ns'] == 7 and result['clock_ns'] == 14
    assert result['winners']['hot']['attempt'] == 'backup'
    assert result['attempts']['original']['retired_ns'] == 13
    assert result['resources']['original-QP-and-source-buffer']['closed_ns'] == 12
    assert [e['outcome'] for e in result['events']].count('committed') == 2
    assert result['events'][-1]['outcome'] == 'late_retired'
    original = json.loads((out/'input.json').read_bytes())
    prefix = deepcopy(original)
    prefix['events'] = prefix['events'][:16]
    (out/'prefix.json').write_text(json.dumps(prefix)+'\n')
    partial = cli('logical-only', 'prefix.json', 'logical-only')
    assert partial['logical_complete_ns'] == 7 and not partial['declared_call_retirable']
    assert partial['resources']['original-QP-and-source-buffer']['closed_ns'] is None
    broken = deepcopy(original)
    broken['events'][15]['key_sha256'] = '0'*64
    (out/'wrong-result.json').write_text(json.dumps(broken)+'\n')
    cli('wrong-result', 'wrong-result.json', 'invalid', 1)
    assert not (out/'invalid').exists()
    saved = (out/'full/report.json').read_bytes()
    cli('refuse-overwrite', 'input.json', 'full', 1)
    assert (out/'full/report.json').read_bytes() == saved
    (out/'relocated').mkdir()
    shutil.copyfile(out/'input.json', out/'relocated/input.json')
    relocated = cli('relocated', 'relocated/input.json', 'relocated-result')
    assert result == relocated
    assert (out/'full/report.json').read_bytes() == (out/'relocated-result/report.json').read_bytes()
    (out/'verification.json').write_text(json.dumps(dict(passed=True, commands=len(commands),
        offline_callback_model_only=True, network_executions=0, logical_complete_ns=7,
        original_resource_closed_ns=12, original_retired_ns=13,
        result_identity_rejected=True, overwrite_rejected=True, relocation_identical=True), indent=2)+'\n')
    print('PASS: contribution callback workflow; no network execution')


if __name__ == '__main__':
    main()
