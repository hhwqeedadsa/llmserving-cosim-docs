"""Check this review's generated page, identities and bounded execution receipts."""
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.tables, self.scripts = [], 0, 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and 'href' in attrs:
            self.refs.append(attrs['href'])
        self.tables += tag == 'table'
        self.scripts += tag == 'script'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    review = ROOT/'docs/reviews/专家副本恢复的已有工作与实施边界_20261030.md'
    page = HERE/'report.html'
    source = review.read_text()
    reader = Links()
    reader.feed(page.read_text())
    need(reader.tables == 3 and reader.scripts == 0, 'reader tables or offline script boundary changed')
    need(sha(review) in page.read_text(), 'page was not generated from current review')
    checked = []
    for base, refs in [(review.parent, re.findall(r'\]\(([^)]+)\)', source)), (page.parent, reader.refs)]:
        for href in refs:
            ref = urlsplit(href)
            if ref.scheme or not ref.path:
                continue
            path = (base/unquote(ref.path)).resolve()
            need(path.is_file(), 'broken local link '+str(path))
            checked.append(str(path.relative_to(ROOT)))
    for path in HERE.glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
    evidence = json.loads((HERE/'evidence.json').read_bytes())
    need(evidence['passed'] and evidence['network_executions'] == 0, 'offline audit missing')
    for name, value in evidence['code'].items():
        need(sha(ROOT/name) == value['sha256'], 'audited platform source changed')
    for name, digest in evidence['saved_execution_inputs'].items():
        need(sha(ROOT/name) == digest, 'saved execution changed')
    monitors = {}
    for path in sorted((ROOT/'runs/replica_feasibility_20261030/monitors').glob('*/probe.json')):
        receipt = json.loads(path.read_bytes())
        need('ended_utc' in receipt and not receipt['remaining_live_group_pids'], 'monitored process not terminal')
        monitors[path.parent.name] = dict(status=receipt['status'], wall_seconds=receipt['wall_seconds'],
            peak_rss_bytes=receipt['peak_sampled_group_rss_bytes'], sha256=sha(path),
            path=str(path.relative_to(ROOT)))
    seconds = sum(r['wall_seconds'] for r in monitors.values())
    need(seconds < 600, 'aggregate monitored wall budget exceeded')
    subprocess.run(['git', 'diff', '--check'], cwd=ROOT, check=True)
    deps = subprocess.run(['bash', 'scripts/check_deps.sh'], cwd=ROOT, capture_output=True, text=True)
    need(deps.returncode == 0 and 'PASS: checked 13 dependency checkouts' in deps.stdout, 'dependency identity failed')
    output = dict(passed=True, network_executions=0, builds=0, gpu_executions=0,
        checked_local_link_count=len(checked), checked_local_targets=sorted(set(checked)),
        source_requests=len(evidence['sources']), total_captured_source_bytes=sum(s.get('captured_bytes', 0) for s in evidence['sources']),
        failed_source_requests=[s['id'] for s in evidence['sources'] if s['effective_status'] != 'retrieved'],
        monitored_seconds_before_closeout=seconds, monitors=monitors,
        dependency_check=dict(exit_code=deps.returncode, stdout=deps.stdout, stderr=deps.stderr),
        artifacts={str(p.relative_to(ROOT)): sha(p) for p in [review, page, HERE/'evidence.json', *sorted(HERE.glob('*.py'))]},
        no_algorithm_or_novelty_claim=True)
    with (HERE/'validation.json').open('x') as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: output[k] for k in ('passed', 'checked_local_link_count', 'source_requests', 'monitored_seconds_before_closeout')}))


if __name__ == '__main__':
    main()
