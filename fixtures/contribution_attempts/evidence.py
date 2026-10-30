"""Collect bounded CPU/CLI evidence; do not run a network or old experiments."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = ROOT/'runs/contribution_attempts_20261030'
    monitors = {}
    for path in sorted((root/'monitors').glob('*/probe.json')):
        row = json.loads(path.read_bytes())
        assert not row['remaining_live_group_pids'] and 'ended_utc' in row
        if path.parent.name == 'evidence':
            assert row['status'] == 'child_failed' and row['child_exit_code'] == 1
        else:
            assert row['status'] == 'completed' and row['child_exit_code'] == 0
        monitors[path.parent.name] = row
    assert {'unit', 'final-unit', 'default-regression', 'workflow'} <= monitors.keys()
    assert 'Ran 14 tests' in (root/'monitors/final-unit/stderr.log').read_text()
    assert 'Ran 21 tests' in (root/'monitors/default-regression/stderr.log').read_text()
    workflow = json.loads((root/'workflow/verification.json').read_bytes())
    assert workflow['passed'] and workflow['network_executions'] == 0 and workflow['commands'] == 5
    report = root/'workflow/full/report.json'
    raw = report.read_bytes()
    assert raw == (root/'workflow/relocated-result/report.json').read_bytes()
    state = json.loads(raw)
    assert state['declared_call_retirable'] and state['logical_complete_ns'] == 7
    assert state['resources']['original-QP-and-source-buffer']['closed_ns'] == 12
    assert state['attempts']['original']['retired_ns'] == 13
    partial = json.loads((root/'workflow/logical-only/report.json').read_bytes())
    assert partial['logical_complete_ns'] == 7 and not partial['declared_call_retirable']
    names = ['python/moe_ub_cosim/contribution_attempts.py', 'python/moe_ub_cosim/cli.py',
        'tests/unit/contribution_attempts_test.py', 'tests/integration/check_contribution_workflow.py',
        'examples/contribution_attempts/input.json', 'examples/contribution_attempts/README.md',
        'docs/contribution_attempts.md', 'fixtures/contribution_attempts/evidence.py']
    for name in names:
        if name.endswith('.py'):
            ast.parse((ROOT/name).read_text())
    help_result = subprocess.run([sys.executable, '-B', '-m', 'moe_ub_cosim', 'scaleout', 'contribution-check', '--help'],
                                cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
    assert 'no network, GPU or timing prediction' in ' '.join(help_result.stdout.split())
    links = []
    # Only check new docs here; historical unrelated links are not this change.
    for name in ('docs/contribution_attempts.md', 'examples/contribution_attempts/README.md'):
        document = ROOT/name
        for href in re.findall(r'\]\(([^)]+)\)', document.read_text()):
            ref = urlsplit(href)
            if ref.scheme:
                continue
            target = (document.parent/unquote(ref.path)).resolve()
            assert target.is_file(), str(target)
            links.append(str(target.relative_to(ROOT)))
    dep = subprocess.run(['bash', 'scripts/check_deps.sh'], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert dep.returncode == 0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    seconds = sum(m['wall_seconds'] for m in monitors.values())
    assert seconds < 600
    receipt = dict(passed=True, source_head_before_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_sha256={name: sha(ROOT/name) for name in names}, unit_tests=14, related_regressions=21,
        arrival_order_permutations=24, workflow=workflow, commands=json.loads((root/'workflow/commands.json').read_bytes()),
        public_help=help_result.stdout, checked_local_links=links,
        dependency_check=dict(exit_code=dep.returncode, stdout=dep.stdout, stderr=dep.stderr),
        monitors_before_evidence=monitors, monitored_seconds_before_evidence=seconds,
        preserved_failure='Initial evidence collection found that subcommand help lacked its scope description; corrected CLI description, no provider or network executed.',
        new_network_executions=0, builds=0, gpu_executions=0, backend_behavior_changed=False,
        real_route_inputs_used=False, model='declared_untimed_artificial_callbacks',
        example_report_sha256=sha(report), scope='logical correctness only; no runtime recovery or benefit claim')
    with (HERE/'example-report.json').open('xb') as stream:
        stream.write(raw)
    with (HERE/'receipt.json').open('x') as stream:
        json.dump(receipt, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps(dict(passed=True, unit_tests=14, related_regressions=21, network_executions=0)))


if __name__ == '__main__':
    main()
