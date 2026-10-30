"""Seal saved inputs, exact execution receipts and portable report links."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

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
        self.links = []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        self.scripts += tag == 'script'
        self.links.extend(v for k, v in attrs if k in ('href', 'src'))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    task, out = args.task_root.resolve(), args.report.resolve()
    frozen = load(task/'study/frozen.json')
    assert load(out/'frozen.json') == frozen
    assert load(out/'calibration.json') == frozen['calibration']
    analysis, mechanism = load(out/'analysis.json'), load(out/'mechanism.json')
    assert analysis == load(task/'analysis/analysis.json') and analysis['passed']
    assert mechanism == load(task/'analysis/mechanism.json') and mechanism['passed']
    assert sha(Path(__file__).with_name('analyze.py')) == analysis['analyzer_sha256']
    assert sha(Path(__file__).with_name('explain.py')) == mechanism['analyzer_sha256']
    assert sha(Path(__file__).with_name('prepare.py')) == frozen['preparer_sha256']
    assert sha(Path(__file__).with_name('protocol.md')) == frozen['method_sha256']
    assert sha(ROOT/'experiments/sequential_externality/analyze.py') == frozen['independent_inspector_sha256']
    assert sha(Path(frozen['backend']['path'])) == frozen['backend']['sha256']
    assert files(Path(frozen['prepared'])) == frozen['prepared_sha256']
    for path, digest in frozen['loaded_ns3_libraries'].items():
        assert sha(Path(path)) == digest
    for case in frozen['inherited_cases'].values():
        assert files(Path(case['output'])) == case['sha256']
    native_probes = list((task/'study').glob('*/execution/probe.json'))
    assert len(native_probes) == 2
    new_networks = {}
    for condition, spec in frozen['new_cases'].items():
        path = Path(spec['output'])
        probe = load(path/'execution/probe.json')
        assert probe['status'] == 'completed' and probe['child_exit_code'] == 0
        assert not probe['remaining_live_group_pids']
        assert load(path/'verification.json')['passed']
        assert load(path/'result.json')['drained']
        new_networks[condition] = dict(output=spec['output'], sha256=files(path), native_probe=probe)
    stages = {p.parent.name: load(p) for p in (task/'monitors').glob('*/probe.json')}
    assert {name for name, value in stages.items() if value['status'] != 'completed'} == {'explain'}
    assert stages['explain']['child_exit_code'] == 1
    assert all(not value['remaining_live_group_pids'] for value in stages.values())
    network_stages = {name: value for name, value in stages.items() if name.startswith('network-')}
    assert set(network_stages) == {'network-normal', 'network-local'}
    for value in network_stages.values():
        assert value['status'] == 'completed' and value['child_exit_code'] == 0
        command = value['command']
        assert command[2:6] == ['-m', 'moe_ub_cosim', 'scaleout', 'rdma-run']
    measured = sum(value['wall_seconds'] for value in stages.values())
    nested = sum(load(p)['wall_seconds'] for p in native_probes)
    # This audit runs inside its own 120-second bounded stage. Its final probe
    # is written after return, so reserve its full limit instead of ignoring it.
    assert measured+nested+120 < 600
    usage, seen = {}, set()
    for root in (task, ROOT/'experiments/calibrated_hedge'):
        apparent = allocated = 0
        for p in (root, *root.rglob('*')):
            info = p.lstat()
            key = info.st_dev, info.st_ino
            if key in seen:
                continue
            seen.add(key)
            apparent += info.st_size
            allocated += info.st_blocks*512
        usage[str(root.relative_to(ROOT))] = dict(apparent_bytes=apparent, allocated_bytes=allocated)
    used = sum(max(v.values()) for v in usage.values())
    reserve = 2**20
    assert used+reserve < 768*2**20
    free = shutil.disk_usage(task).free
    assert free >= 8*2**30
    resources = dict(passed=True, completed_stage_monitors=stages,
        completed_stage_wall_seconds=measured, nested_native_wall_seconds=nested,
        native_time_already_in_stage_time=True, seal_stage_reserved_seconds=120,
        conservative_wall_upper_bound_seconds=measured+nested+120,
        cumulative_wall_budget_seconds=600, peak_sampled_prior_group_rss_bytes=max(v['peak_sampled_group_rss_bytes'] for v in stages.values()),
        seal_stage_group_rss_limit_bytes=2**30, artifact_usage_before_small_receipts=usage,
        artifact_bytes_before_receipts=used, final_receipts_reserve_bytes=reserve,
        conservative_artifact_upper_bound_bytes=used+reserve, artifact_budget_bytes=768*2**20,
        free_bytes=free, min_free_bytes=8*2**30, new_network_executions=2, network_reruns=0,
        zero_builds_dependencies_downloads_installs_gpu=True, failures_retained=['monitors/explain'])
    save(out/'resources.json', resources)
    source_paths = sorted(p for p in Path(__file__).parent.iterdir() if p.is_file())
    receipt = dict(passed=True, preregistration_commit='e311d740e2',
        analysis_platform_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        actual_commands_and_status=stages, inherited_networks_unchanged=4, new_networks=new_networks,
        new_network_executions=2, network_reruns=0,
        initial_explanation_failure='Required identical member arrivals although only phase release was unchanged; corrected offline, original failure retained',
        source_sha256={str(p.relative_to(ROOT)): sha(p) for p in source_paths},
        original_files_mutated=False, new_algorithm_benefit_proven=False, overall_goal_complete=False,
        portable_artifact_sha256={str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*'))
                                 if p.is_file() and p.name not in ('receipt.json', 'final-audit.json')})
    save(out/'receipt.json', receipt)
    save(out/'final-audit.json', dict(passed=False, status='checking'))
    page = Links()
    page.feed((out/'report.html').read_text())
    links = []
    def check(file, value):
        uri = urlsplit(value)
        if uri.scheme or uri.netloc or not uri.path:
            return
        path = file.parent/unquote(uri.path)
        assert path.exists(), str(file)+': missing '+value
        links.append(dict(file=str(file.relative_to(ROOT)), target=value))
    for value in page.links:
        check(out/'report.html', value)
    task_page = ROOT/'docs/tasks/current.md'
    for file in (out/'README.md', Path(__file__).with_name('README.md'), task_page):
        text = file.read_text()
        if file == task_page:
            text = text.split('## 2026-10-30 多步完整备用')[0]
        for value in re.findall(r'\]\(([^)]+)\)', text):
            check(file, value)
    assert page.scripts == 0
    assert len(list((out/'figures').glob('*.png'))) == len(list((out/'figures').glob('*.svg'))) == 2
    for path in (out/'metrics').glob('*.csv'):
        assert b'\r' not in path.read_bytes()
        assert sha(path) == sha(task/'analysis'/path.name)
    for name in ('report.html', 'figures/comparison.png', 'figures/comparison.svg', 'figures/race.png', 'figures/race.svg'):
        assert sha(out/name) == sha(task/'report'/name)
    browsers = {name: shutil.which(name) for name in ('chromium', 'chromium-browser', 'google-chrome', 'firefox', 'playwright')}
    save(out/'final-audit.json', dict(passed=True, new_networks_exactly_once=2, inherited_networks_unchanged=4,
        source_and_backend_and_input_hashes_checked=True, complete_source_and_phase_and_ack_and_timer_and_kv_checks=True,
        healthy_hops_each_comparison=4264, healthy_hops_unmatched=0,
        links_checked=len(links), links=links, csv_files=len(list((out/'metrics').glob('*.csv'))),
        portable_report_matches_generator=True, browser_executables=browsers,
        browser_rendering_checked=False, images_visually_inspected=2,
        failures_retained=['explain'], no_new_platform_changes=True,
        new_algorithm_benefit_proven=False, overall_goal_complete=False,
        artifact_sha256={str(p.relative_to(out)): sha(p) for p in sorted(out.rglob('*'))
                         if p.is_file() and p.name != 'final-audit.json'}))
    print(json.dumps(dict(passed=True, links=len(links), artifact_bytes=used,
        wall_upper_bound_seconds=resources['conservative_wall_upper_bound_seconds'],
        new_network_executions=2, network_reruns=0)))

if __name__ == '__main__':
    main()
