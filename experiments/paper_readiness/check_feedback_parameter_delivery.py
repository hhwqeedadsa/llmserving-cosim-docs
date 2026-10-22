"""Read-only delivery checks for the completed fixed parameter-dependence study."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import tarfile

from moe_ub_cosim.host_probe import disk_usage

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = self.rows = 0
        self.links = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in ('html', 'head', 'body', 'table', 'tr', 'td', 'div'):
            self.stack.append(tag)
        if tag == 'table':
            self.tables += 1
        if tag == 'tr':
            self.rows += 1
        if tag == 'a':
            self.links.append(dict(attrs)['href'])

    def handle_endtag(self, tag):
        if tag in ('html', 'head', 'body', 'table', 'tr', 'td', 'div'):
            assert self.stack.pop() == tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('task', 'report', 'baseline', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    task, report, baseline = args.task.resolve(), args.report.resolve(), args.baseline.resolve()
    assert not args.output.exists()
    # Syntax-only checks; no imports execute workload generators or create pycache.
    syntax_files = []
    for path in sorted((ROOT / 'experiments/paper_readiness').glob('*.py')):
        ast.parse(path.read_text(), filename=str(path))
        syntax_files.append(str(path.relative_to(ROOT)))
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    assert load(task / 'deps-monitor/probe.json')['child_exit_code'] == 0
    assert load(task / 'deps-monitor/probe.json')['status'] == 'completed'
    members = load(task / 'checkpoint/members.json')
    receipt = load(report / 'archive.json')
    archive = Path(receipt['path'])
    assert sha(archive) == receipt['sha256'] and archive.stat().st_size <= 48 * 2**20
    assert sum(m['bytes'] for m in members.values()) <= 96 * 2**20
    source_count = report_count = 0
    for name, entry in members.items():
        path = Path(entry['source'])
        assert path.stat().st_size == entry['bytes'] and sha(path) == entry['sha256'], name
        source_count += name.startswith('source/')
        report_count += name.startswith('report/')
    verified = set()
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            if member.name == 'members.json':
                assert json.load(tar.extractfile(member)) == members
                continue
            assert member.name not in verified
            entry = members[member.name]
            data = tar.extractfile(member).read()
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
            verified.add(member.name)
    assert verified == members.keys()
    assert (task / 'compat/verification.json').read_bytes() == (baseline / 'checked-refined/verification.json').read_bytes()
    erratum = load(report / 'erratum.json')
    assert sha(Path(erratum['historical_archive']['path'])) == erratum['historical_archive']['sha256']
    for entry in erratum['corrected_files']:
        assert sha(ROOT / entry['path']) == entry['corrected_sha256']
        assert sha(task / 'citation-history' / entry['path']) == entry['historical_sha256']
    assert sha(baseline / 'analysis/summary.json') == erratum['historical_summary_sha256']
    summary, verification = load(report / 'summary.json'), load(report / 'verification.json')
    assert sha(report / 'summary.json') == sha(task / 'analysis/summary.json')
    assert sha(report / 'verification.json') == sha(task / 'checked/verification.json')
    assert verification['verified'] and verification['all_declared_completed']
    assert len(summary['cases']) == 8 and len(summary['effects']) == 12
    assert summary['new_network_runs'] == 6 and summary['reused_successful_runs'] == 2
    assert all(c['finish_ns'] == c['acked_ns'] for c in summary['cases'])
    for path in report.glob('*.json'):
        load(path)
    document = ReportParser()
    document.feed((report / 'report.html').read_text())
    document.close()
    assert document.tables == 2 and document.rows == 22 and not document.stack
    for link in document.links:
        target = report / link
        assert target == args.output.resolve() or target.is_file(), link
    monitors = []
    for path in sorted(task.glob('*-monitor/probe.json')):
        if path.parent.name == 'final-monitor':
            continue  # The host probe writes its completed record after this child exits.
        record = load(path)
        assert record['status'] == 'completed' and record['child_exit_code'] == 0
        assert not record['remaining_live_group_pids']
        monitors.append(dict(stage=path.parent.name, seconds=record['wall_seconds']))
    seconds = sum(m['seconds'] for m in monitors)
    assert seconds < 240  # Plus this command's hard 60-second cap remains below 300 seconds.
    seen = set()
    usage = {key: 0 for key in ('apparent_bytes', 'allocated_bytes')}
    for root in (task, report):
        for key, value in disk_usage(root, seen).items():
            usage[key] += value
    assert max(usage.values()) < 128 * 2**20
    result = dict(verified=True, archive_sha256=receipt['sha256'], verified_members=len(verified),
        source_files_match_snapshot=source_count, report_files_match_snapshot=report_count,
        syntax_files=len(syntax_files), git_diff_check=True, fixed_dependencies=13,
        old_oracle_verification_byte_identical=True, historical_archive_unchanged=True,
        explicit_erratum_verified=True, html_tables=2, html_rows=22, report_json_valid=True,
        new_network_runs=6, hash_verified_reused_successes=2, additional_network_runs_in_delivery=0,
        stage_monitors=monitors, monitored_seconds_before_final_stage=seconds,
        final_stage_hard_wall_limit_seconds=60, task_and_report_usage_before_final_receipt=usage,
        raw_volatile=True, persistent_backup=False, paper_main_contribution_established=False)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(verified=True, members=len(verified), source_files=source_count, seconds_before_final=seconds)))


if __name__ == '__main__':
    main()
