"""Reproduce the README workflow in an isolated source checkout and new venv.

No old build/run/venv is copied. Pinned Git objects and system Python packages
can be reused locally; this tests independence from historic generated outputs,
not internet availability or portability to another Linux distribution.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args(); root = Path(__file__).resolve().parents[2]
    out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    commands = []; checkout = out/'checkout'

    def run(label, argv, cwd):
        argv = list(map(str, argv)); record = dict(argv=argv, cwd=str(cwd), started_utc=datetime.now(timezone.utc).isoformat())
        environment = dict(os.environ, PIP_NO_CACHE_DIR='1'); environment.pop('PYTHONPATH', None)
        with (out/(label+'.stdout.log')).open('x') as stdout, (out/(label+'.stderr.log')).open('x') as stderr:
            result = subprocess.run(argv, cwd=cwd, stdout=stdout, stderr=stderr, env=environment)
        record.update(exit_code=result.returncode, stdout=str(out/(label+'.stdout.log')), stderr=str(out/(label+'.stderr.log')))
        commands.append(record); (out/'commands.json').write_text(json.dumps(commands, indent=2)+'\n')
        assert result.returncode == 0, (label, record['stderr'])

    commit = subprocess.check_output(['git', '-C', root, 'rev-parse', 'HEAD'], text=True).strip()
    run('clone-root', ['git', 'clone', '--shared', '--no-checkout', root, checkout], root)
    run('checkout-root', ['git', 'checkout', '--detach', commit], checkout)
    modules = subprocess.check_output(['git', '-C', root, 'submodule', 'status', '--recursive'], text=True).splitlines()
    for ordinal, row in enumerate(modules):
        revision, name = row.strip().split()[:2]
        assert len(revision) == 40
        run(f'clone-dependency-{ordinal}', ['git', 'clone', '--shared', '--no-checkout', root/name, checkout/name], root)
        run(f'checkout-dependency-{ordinal}', ['git', 'checkout', '--detach', revision], checkout/name)
    for ordinal, path in enumerate([checkout, *(checkout/row.strip().split()[1] for row in modules)]):
        if (path/'.gitmodules').exists():
            run(f'initialize-metadata-{ordinal}', ['git', 'submodule', 'init'], path)
    assert not any((checkout/name).exists() for name in ('runs', 'build', '.venv'))
    run('venv', ['python3', '-m', 'venv', '--system-site-packages', checkout/'.venv/user'], checkout)
    python = checkout/'.venv/user/bin/python'; command = checkout/'.venv/user/bin/moe-ub'
    run('install', [python, '-m', 'pip', 'install', '--no-build-isolation', '-e', '.[frontend]'], checkout)
    run('import-location', [python, '-c', 'import moe_ub_cosim; print(moe_ub_cosim.__file__)'], checkout)
    assert str(checkout/'python/moe_ub_cosim') in (out/'import-location.stdout.log').read_text()
    run('doctor', [command, 'doctor'], checkout)
    run('build', [command, 'build', '--build-dir', 'build/default', '--jobs', '2'], checkout)
    operations = [
        ('run', ['run', '--config', 'examples/first_round/run.json', '--output', 'runs/demo-run']),
        ('freeze', ['slice', 'create', '--from-run', 'runs/demo-run', '--output', 'data/demo-slice']),
        ('inspect', ['slice', 'inspect', '--slice', 'data/demo-slice', '--verify']),
        ('sample', ['sample', '--slice', 'data/demo-slice', '--config', 'examples/first_round/sample.json', '--output', 'runs/demo-10us']),
        ('replay', ['replay', '--slice', 'data/demo-slice', '--config', 'examples/first_round/replay.json', '--output', 'runs/demo-replay']),
        ('validate-run', ['validate', '--run', 'runs/demo-replay']),
        ('validate-observed', ['validate', '--observed', 'runs/demo-10us/observed'])]
    for label, argv in operations:
        run(label, [command, *argv], checkout)
    # The installed console command also resolves config-relative paths when
    # launched outside the checkout, without PYTHONPATH or a sourced shell.
    run('outside-cwd', [command, 'run', '--config', checkout/'examples/first_round/run.json',
                       '--output', out/'outside-run'], out)
    (out/'summary.json').write_text(json.dumps(dict(passed=True, commit=commit, dependencies=len(modules),
        used_original_build_or_runs_or_venv=False, reused_verified_local_git_objects=True,
        new_venv_with_system_site_packages=True, no_cross_distribution_claim=True,
        command_count=len(commands)), indent=2)+'\n')
    print('PASS: isolated checkout, fresh backend/venv and README public operations')


if __name__ == '__main__':
    main()
