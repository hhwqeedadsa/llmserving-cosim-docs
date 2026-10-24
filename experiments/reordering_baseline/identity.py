"""Read-only execution/source identity check, separate from offline evidence replay."""
import argparse
import hashlib
from pathlib import Path
import subprocess
from experiments.reordering_baseline.stage import load, save


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    for k in ('repository', 'runtime', 'output'): p.add_argument('--'+k, type=Path, required=True)
    a = p.parse_args(); assert not a.output.exists(); r = load(a.runtime); repo = a.repository.resolve()
    assert sha(Path(r['backend'])) == r['sha256']
    assert len(r['libraries']) == 8 and all(sha(Path(name)) == value for name, value in r['libraries'].items())
    b = r['build_identity']; assert all(sha(repo/name) == value for name, value in b['sources'].items())
    assert all(sha(Path(name)) == value for name, value in b['inputs'].items())
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip() == r['platform_commit']
    subprocess.run(['git', 'diff', '--exit-code', r['platform_commit'], '--', 'python', 'backend', 'deps.lock.json', '.gitmodules'], cwd=repo, check=True)
    save(a.output, dict(passed=True, platform_commit=r['platform_commit'], backend_sha256=r['sha256'],
        libraries=8, compiled_sources=len(b['sources']), preserved_build_inputs=len(b['inputs']),
        native_runtime_sources_and_platform_unchanged=True, network_executions=0))


if __name__ == '__main__': main()
