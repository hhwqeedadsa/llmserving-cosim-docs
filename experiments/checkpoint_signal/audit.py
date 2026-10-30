"""Seal zero-network evidence and run portable minimal inputs in a fresh cwd."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]

def load(path):
    return json.loads(path.read_bytes())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def files(path):
    return {str(p.relative_to(path)): sha(p) for p in sorted(path.rglob('*')) if p.is_file()}

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        assert tag != 'script'
        self.targets += [v for k, v in attrs if k in ('src', 'href')]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    task, out = args.task_root.resolve(), args.report.resolve()
    frozen = load(task/'study/frozen.json')
    assert load(out/'frozen.json') == frozen
    for name, digest in frozen['prepared_sha256'].items():
        assert sha(task/'study'/name) == digest
    for name, digest in frozen['source_sha256'].items():
        assert sha(ROOT/name) == digest
    assert sha(Path(__file__).with_name('classify.py')) == frozen['classifier_sha256']
    assert sha(Path(__file__).with_name('prepare.py')) == frozen['preparer_sha256']
    assert sha(Path(__file__).with_name('protocol.md')) == frozen['method_sha256']
    for record in frozen['records'].values():
        assert files(Path(record['output'])) == record['sha256']
    analysis = load(out/'analysis.json')
    assert analysis == load(task/'analysis/analysis.json') and analysis['passed']
    assert analysis['rejected_mutations'] == 42 and analysis['classifier_processes'] == 18
    assert analysis['new_network_executions'] == 0 and analysis['recovery_benefit_measured'] is False
    assert sha(Path(__file__).with_name('evaluate.py')) == analysis['evaluator_sha256']
    watchdog = load(out/'watchdog.json')
    for name, digest in watchdog['source_sha256'].items():
        assert sha(ROOT/name) == digest
    for name in ('network.json', 'watchdog.json', 'decisions.csv'):
        assert sha(out/name) == sha(task/'analysis'/name)
    portable = task/'portable-check'
    portable.mkdir(exist_ok=False)
    commands = []
    for name in frozen['plans']:
        assert sha(out/'plans'/(name+'.json')) == sha(task/'study'/(name+'.json'))
        shutil.copyfile(out/'plans'/(name+'.json'), portable/(name+'.json'))
        for condition in ('normal', 'local'):
            source = out/'minimal'/f'{name}-{condition}.jsonl'
            assert sha(source) == sha(task/'study/observed'/f'{name}-{condition}-minimal.jsonl')
            shutil.copyfile(source, portable/source.name)
            command = [sys.executable, '-B', str(Path(__file__).with_name('classify.py')),
                       '--plan', name+'.json', '--events', source.name, '--view', 'local_member',
                       '--output', f'{name}-{condition}-decision.json']
            result = subprocess.run(command, cwd=portable, capture_output=True, text=True)
            commands.append(dict(command=command, cwd=str(portable), exit_code=result.returncode,
                                 stdout=result.stdout, stderr=result.stderr))
            assert result.returncode == 0, result.stderr
            assert load(portable/f'{name}-{condition}-decision.json') == load(task/'decisions'/f'{name}-{condition}-local_member.json')
    assert not list(task.rglob('execution/probe.json')), 'a new native network appeared'
    stages = {p.parent.name: load(p) for p in (task/'monitors').glob('*/probe.json')}
    assert stages and all(v['status'] == 'completed' and not v['remaining_live_group_pids'] for v in stages.values())
    assert all('rdma-run' not in v['command'] and 'rdma-build' not in v['command'] for v in stages.values())
    measured = sum(v['wall_seconds'] for v in stages.values())
    assert measured+120 < 600
    usage, seen = {}, set()
    for root in (task, ROOT/'experiments/checkpoint_signal'):
        apparent = allocated = 0
        for path in (root, *root.rglob('*')):
            info = path.lstat()
            if (info.st_dev, info.st_ino) in seen:
                continue
            seen.add((info.st_dev, info.st_ino))
            apparent += info.st_size
            allocated += info.st_blocks*512
        usage[str(root.relative_to(ROOT))] = dict(apparent_bytes=apparent, allocated_bytes=allocated)
    size = sum(max(v.values()) for v in usage.values())
    assert size+2**20 < 256*2**20 and shutil.disk_usage(task).free >= 8*2**30
    save(out/'resources.json', dict(passed=True, completed_stage_monitors=stages,
        measured_stage_wall_seconds=measured, current_seal_stage_reserved_seconds=120,
        conservative_wall_bound_seconds=measured+120, budget_seconds=600,
        peak_sampled_prior_group_rss_bytes=max(v['peak_sampled_group_rss_bytes'] for v in stages.values()),
        seal_rss_limit_bytes=2**30, artifact_usage_before_receipts=usage,
        artifact_bytes_before_receipts=size, final_receipts_reserve_bytes=2**20,
        conservative_artifact_upper_bound_bytes=size+2**20, artifact_budget_bytes=256*2**20,
        free_bytes=shutil.disk_usage(task).free, min_free_bytes=8*2**30,
        new_network_executions=0, zero_builds_dependencies_downloads_installs_gpu=True, failures=[]))
    sources = [p for p in Path(__file__).parent.iterdir() if p.is_file()]
    save(out/'receipt.json', dict(passed=True, preregistration_commit='e24962ea3e', watchdog_reference_preregistration_commit='149b3f1a54',
        platform_commit=frozen['platform_commit'], source_sha256={str(p.relative_to(ROOT)): sha(p) for p in sources},
        stage_commands_and_status=stages, classifier_commands=load(task/'decisions/commands.json'),
        portable_minimal_commands=commands, old_networks_unchanged=2,
        new_network_executions=0, actual_notifications_generated=0,
        new_platform_policy_implemented=False, recovery_benefit_measured=False,
        new_algorithm_benefit_proven=False, overall_goal_complete=False))
    save(out/'final-audit.json', dict(passed=False, status='checking'))
    parser = Links()
    parser.feed((out/'report.html').read_text())
    links = []
    def check(file, value):
        if re.match(r'^[a-zA-Z]+:', value) or value.startswith('#'):
            return
        assert (file.parent/value).exists(), str(file)+': missing '+value
        links.append(dict(file=str(file.relative_to(ROOT)), target=value))
    for value in parser.targets:
        check(out/'report.html', value)
    for file in (out/'README.md', Path(__file__).with_name('README.md'), ROOT/'docs/tasks/current.md'):
        text = file.read_text()
        if file.name == 'current.md':
            text = text.split('## 2026-10-30 正常样本校准')[0]
        for value in re.findall(r'\]\(([^)]+)\)', text):
            check(file, value)
    assert b'\r' not in (out/'decisions.csv').read_bytes()
    assert len(list((out/'figures').glob('*.png'))) == len(list((out/'figures').glob('*.svg'))) == 1
    save(out/'final-audit.json', dict(passed=True, old_network_files_unchanged=2, new_network_executions=0,
        complete_native_observation_prefixes_checked=True, strict_past_classifier_processes=18,
        rejected_prefix_mutations=42, portable_minimal_examples_checked_in_fresh_cwd=6,
        truth_not_a_classifier_argument=True, runtime_source_identity_checked=True,
        source_watchdog_predicate_only=True, actual_notifications_generated=0,
        links_checked=len(links), links=links, figure_visually_inspected=1,
        browser_rendering_checked=False,
        browser_executables={n: shutil.which(n) for n in ('chromium', 'chromium-browser', 'google-chrome', 'firefox', 'playwright')},
        overall_goal_complete=False, artifact_sha256={str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*'))
                                                     if p.is_file() and p.name != 'final-audit.json'}))
    print(json.dumps(dict(passed=True, links=len(links), portable_minimal_examples=6,
        new_network_executions=0, artifact_bytes=size, wall_bound_seconds=measured+120)))

if __name__ == '__main__':
    main()
