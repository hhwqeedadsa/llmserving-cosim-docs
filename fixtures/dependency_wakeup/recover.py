"""Execute only bundled Python sources and saved records in an empty cwd."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restored', type=Path, required=True)
    args = parser.parse_args()
    root = args.restored.resolve()
    env = dict(os.environ, PYTHONPATH=str(root/'source/python')+':'+str(root/'source'), PYTHONDONTWRITEBYTECODE='1')
    commands = [
        [sys.executable, '-P', '-B', '-m', 'fixtures.dependency_wakeup.audit', '--source', str(root/'original'),
         '--output', str(root/'fresh-audit.json')],
        [sys.executable, '-P', '-B', '-m', 'fixtures.dependency_wakeup.verify', '--source', str(root/'original'),
         '--report', str(root/'fresh-audit.json'), '--output', str(root/'fresh-verification.json')],
    ]
    assert all(not (root/name).exists() for name in ('fresh-audit.json', 'fresh-verification.json', 'recovery.json'))
    for index, command in enumerate(commands):
        with (root/f'fresh-{index}.stdout').open('w') as stdout, (root/f'fresh-{index}.stderr').open('w') as stderr:
            subprocess.run(command, cwd=root/'empty-cwd', env=env, stdout=stdout, stderr=stderr, check=True, timeout=25)
    comparisons = {}
    for fresh, old in (('fresh-audit.json', 'audit-evidence.json'), ('fresh-verification.json', 'verification.json')):
        data = (root/fresh).read_bytes()
        assert data == (root/'evidence'/old).read_bytes(), 'fresh offline result changed'
        comparisons[fresh] = hashlib.sha256(data).hexdigest()
    result = dict(passed=True, network_executed=False, fresh_source_root=True, empty_cwd=True,
                   reports_byte_identical=comparisons, commands=commands,
                   scope='offline callback replay only; no native network execution')
    (root/'recovery.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
