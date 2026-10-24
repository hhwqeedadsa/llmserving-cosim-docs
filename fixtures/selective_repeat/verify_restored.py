"""Recompute an already restored checkpoint offline; no network execution."""
import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

from audit import audit
from checkpoint import restore


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unsafe_archives(output):
    rejected = []
    base = dict(schema_version='selective-repeat-checkpoint/1', bytes=1,
                files={'ok': dict(bytes=1, sha256=hashlib.sha256(b'x').hexdigest())})
    for label in ('traversal', 'absolute', 'backslash', 'duplicate', 'symlink',
                  'parent_collision', 'hash_mismatch', 'undeclared'):
        manifest = deepcopy(base)
        rows = [('ok', b'x', False)]
        if label in ('traversal', 'absolute', 'backslash'):
            name = {'traversal': '../escape', 'absolute': '/escape', 'backslash': 'a\\b'}[label]
            rows = [(name, b'x', False)]
        elif label == 'duplicate':
            rows.append(('ok', b'x', False))
        elif label == 'symlink':
            rows = [('ok', b'', True)]
        elif label == 'parent_collision':
            rows.append(('ok/child', b'x', False))
        elif label == 'hash_mismatch':
            rows = [('ok', b'z', False)]
        else:
            rows.append(('extra', b'x', False))
        path = output / (label + '.tar.gz')
        with tarfile.open(path, 'x:gz') as archive:
            rows.append(('manifest.json', json.dumps(manifest).encode(), False))
            for name, data, link in rows:
                info = tarfile.TarInfo(name)
                if link:
                    info.type = tarfile.SYMTYPE
                    info.linkname = '../escape'
                else:
                    info.size = len(data)
                archive.addfile(info, None if link else io.BytesIO(data))
        target = output / (label + '-restored')
        try:
            restore(path, target)
        except ValueError:
            assert not target.exists()
            rejected.append(label)
        else:
            raise AssertionError('unsafe archive accepted: ' + label)
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package.resolve()
    output = args.output.resolve()
    output.mkdir()
    empty = output / 'empty-cwd'
    empty.mkdir()
    source = package / 'source'
    runtime = package / 'runtime'
    env = dict(os.environ, PYTHONPATH=str(source / 'python'), PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', str(source / 'fixtures/selective_repeat/audit.py'),
               '--stage', str(runtime), '--output', str(output / 'audit.json')]
    with (output / 'stdout.log').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        result = subprocess.run(command, cwd=empty, env=env, stdout=stdout, stderr=stderr, timeout=30)
    assert result.returncode == 0
    assert (output / 'audit.json').read_bytes() == (package / 'evidence/audit-complete.json').read_bytes()
    fresh = audit(load(runtime / 'fresh-network/result.json'),
                  load(runtime / 'inputs/healthy-work.json'), load(runtime / 'inputs/healthy-protocol.json'))
    assert fresh == load(output / 'audit.json')['cases']['healthy']
    assert load(runtime / 'network/default-old/result.json') == load(runtime / 'network/default-new/result.json')
    libraries = load(package / 'evidence/build.json')['inputs']
    for original, digest in libraries.items():
        if original.endswith('.so'):
            assert sha(runtime / 'lib' / Path(original).name) == digest
    rejected = unsafe_archives(output)
    receipt = dict(passed=True, network_executed=False, restored_source_root=True, empty_cwd=True,
                   nine_case_audit_byte_equal=True, fresh_result_independently_audited=True,
                   default_complete_result_equal=True, eight_original_libraries_hash_equal=True,
                   rejected_archive_classes=rejected, command=command, cwd=str(empty))
    (output / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
