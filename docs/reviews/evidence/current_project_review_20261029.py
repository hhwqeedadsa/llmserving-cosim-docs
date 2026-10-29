"""Read-only current source inventory and public-summary metric audit.

Does not run networks, load raw routes, import tests, or modify saved runs.
Optional --run arguments need saved SimAI service input/result/frontend files.
The output is review evidence, not an algorithm observation or hardware test.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def read(path, maximum=128 * 2**20):
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f'missing or oversized review input: {path}')
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=15).strip()


def inventory():
    paths = git('ls-files', 'python/moe_ub_cosim', 'backend', 'tests/unit',
                'tests/integration', 'experiments', '.github', 'AGENTS.md').splitlines()
    modules = [ROOT / p for p in paths if p.startswith('python/moe_ub_cosim/') and p.endswith('.py')]
    violations = []
    for path in modules:
        tree = ast.parse(path.read_bytes(), filename=str(path))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ''] if isinstance(node, ast.ImportFrom) and not node.level else [])
            for name in names:
                if name.split('.')[0] in ('experiments', 'tests'):
                    violations.append(dict(file=str(path.relative_to(ROOT)), line=node.lineno, module=name))
    return dict(tracked_python_modules_parsed=len(modules), direct_forbidden_imports=violations,
                import_check_scope='AST import statements; dynamic and transitive imports not certified',
                backend_source_files=sum(p.startswith('backend/') and p.endswith(('.cc', '.hh')) for p in paths),
                tracked_unit_sources=sum(p.startswith('tests/unit/') and p.endswith(('.py', '.cc')) for p in paths),
                tracked_integration_sources=sum(p.startswith('tests/integration/') and p.endswith(('.py', '.cc')) for p in paths),
                github_workflows=[p for p in paths if p.startswith('.github/workflows/')])


def audit_summary(directory):
    from moe_ub_cosim.run_summary import describe

    directory = directory.resolve()
    frontend = read(directory / 'frontend.json')
    result = read(directory / 'result.json')
    if not result['passed']:
        raise ValueError('this diagnostic requires a successful saved service run')
    history = frontend['history']
    if not history or any(type(row.get('finished_ns')) is not int for row in history):
        raise ValueError('missing actual graph completion')
    app = max(row['finished_ns'] for row in history)
    final = [row for row in frontend['transcript'] if row['reply']['service_finished']]
    if len(final) != 1:
        raise ValueError('missing or repeated final frontend COMMIT')
    decision = final[0]
    if not (decision['event']['now_ns'] == decision['reply']['now_ns'] == app <= result['finish_ns']):
        raise ValueError('application/control/drain timestamps disagree')
    if not (result['drained'] and result['service']['service_finished'] and
            result['service']['active_graphs'] == 0 and
            max(row['finished_ns'] for row in result['service']['waves']) == app):
        raise ValueError('application/native graph completion disagrees')
    public = describe(directory)
    # Preserve the public result, including ambiguous/absent names, as evidence.
    return dict(run=directory.name, result_sha256=sha(directory / 'result.json'),
                frontend_sha256=sha(directory / 'frontend.json'),
                public_outcome_fields=sorted(public['outcome']),
                public_finish_ns=public['outcome']['finish_ns'],
                independently_checked_application_finished_ns=app,
                network_drained_ns=result['finish_ns'],
                post_application_drain_ns=result['finish_ns'] - app,
                public_has_application_finished_ns='application_finished_ns' in public['outcome'],
                public_has_network_drained_ns='network_drained_ns' in public['outcome'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('review output already exists')
    facts = dict(schema_version='moe-current-project-review/1', source_head=git('rev-parse', 'HEAD'),
                 branch=git('branch', '--show-current'), new_network_runs=0,
                 inventory=inventory(), saved_summary_audits=[audit_summary(d) for d in args.run])
    sources = ['python/moe_ub_cosim/run_summary.py', 'python/moe_ub_cosim/scaleout_verify.py',
               'python/moe_ub_cosim/simai_verify.py', 'python/moe_ub_cosim/scaleout_service.py',
               'python/moe_ub_cosim/scaleout_messages.py', 'backend/rdma/astra_adapter.cc',
               'backend/rdma/fabric.cc', 'backend/rdma/persistent_streams.cc',
               'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-node.cc']
    facts['source_sha256'] = {p: sha(ROOT / p) for p in sources}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        stream.write(json.dumps(facts, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(modules=facts['inventory']['tracked_python_modules_parsed'],
                          saved_runs_checked=len(facts['saved_summary_audits']), new_network_runs=0)))


if __name__ == '__main__':
    main()
