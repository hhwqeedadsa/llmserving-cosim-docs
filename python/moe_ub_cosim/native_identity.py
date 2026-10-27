"""Read the declared native dependency before checking the actual checkout.

Reading HEAD alone cannot establish which source was intended. Pure input
validation does not call this module and needs no Git or dependency checkout.
"""
import json
from pathlib import Path
import re
import subprocess

from .scaleout_topology import require


NATIVE_PATH = 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
PARENT_PATH = 'third_party/LLMServingSim/astra-sim'
SUBMODULE_PATH = 'extern/network_backend/ns-3'


def locked_native_commit(repository):
    path = Path(repository) / 'deps.lock.json'
    require(path.is_file() and path.stat().st_size <= 1048576,
            'native dependency requires deps.lock.json (at most 1MiB)')
    lock = json.loads(path.read_text())
    require(isinstance(lock, dict) and lock.get('schema_version') == 'moe-ub-deps/1'
            and isinstance(lock.get('dependencies'), list), 'invalid native dependency lock')
    rows = [row for row in lock['dependencies'] if isinstance(row, dict) and
            (row.get('name') == 'ASTRA-original-ns3-backend' or row.get('path') == NATIVE_PATH)]
    require(len(rows) == 1, 'expected one locked ASTRA-original-ns3-backend dependency')
    row = rows[0]
    require(row.get('name') == 'ASTRA-original-ns3-backend' and row.get('path') == NATIVE_PATH
            and row.get('parent_path') == PARENT_PATH and row.get('submodule_path') == SUBMODULE_PATH,
            'native dependency lock has unexpected parent or path')
    commit = row.get('checkout_commit')
    require(isinstance(commit, str) and re.fullmatch('[0-9a-f]{40}', commit) is not None,
            'native dependency lock requires a full lowercase checkout_commit')
    return commit


def verify_native_source(repository):
    repository = Path(repository)
    expected = locked_native_commit(repository)
    def git(directory, *args):
        result = subprocess.run(['git', *args], cwd=directory, capture_output=True, text=True)
        require(result.returncode == 0, 'cannot inspect native Git identity: ' + str(directory))
        return result.stdout.strip()
    entry = git(repository / PARENT_PATH, 'ls-tree', 'HEAD', '--', SUBMODULE_PATH).split()
    require(entry == ['160000', 'commit', expected, SUBMODULE_PATH],
            'native dependency lock differs from its parent gitlink')
    native = repository / NATIVE_PATH
    require(git(native, 'rev-parse', 'HEAD') == expected, 'native ns-3 differs from locked checkout_commit')
    require(not git(native, 'status', '--porcelain', '--untracked-files=no'),
            'tracked native ns-3 source is dirty')
    return expected
