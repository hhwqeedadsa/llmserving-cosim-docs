"""Verify the current screening checkpoint, not overall paper readiness."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / 'docs/review_artifacts/paper_readiness_20261022'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links += [v for k, v in attrs if k in ('href', 'src')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output.mkdir(exist_ok=False)
    commands = []
    exp = json.loads((a.input / 'experiment.json').read_text())
    for i, case in enumerate(exp['cases'].values()):
        run = a.input / case['run']
        dest = a.output / f'diagnosis-{i:02}.json'
        argv = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', 'rdma-diagnose', '--run', str(run), '--output', str(dest)]
        with (a.output / f'{i:02}.stdout').open('w') as f, (a.output / f'{i:02}.stderr').open('w') as e:
            completed = subprocess.run(argv, cwd=ROOT, stdout=f, stderr=e, timeout=30)
        commands.append(dict(argv=argv, exit_code=completed.returncode, cwd=str(ROOT)))
        (a.output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        assert completed.returncode == 0
        diagnosis = json.loads(dest.read_text())
        assert diagnosis['passed'] and diagnosis['admission_resources']['reconstructed']
    replay = a.output / 'offline-repeat'
    argv = [sys.executable, str(Path(__file__).parent / 'analyze.py'), '--input', str(a.input), '--output', str(replay)]
    completed = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)
    (a.output / 'repeat.stdout').write_text(completed.stdout)
    (a.output / 'repeat.stderr').write_text(completed.stderr)
    assert completed.returncode == 0
    for name in ('summary.json', 'results.md'):
        assert sha(replay / name) == sha(a.analysis / name) == sha(REVIEW / name)
    claims = json.loads((REVIEW / 'claims.json').read_text())
    for claim in claims['claims']:
        for evidence in claim['evidence']:
            assert sha(ROOT / evidence['path']) == evidence['sha256']
    sources = json.loads((REVIEW / 'sources.json').read_text())
    for source in sources['reused_primary_sources']:
        assert sha(ROOT / source['local_snapshot']) == source['sha256']
        assert sha(ROOT / source['text_snapshot']) == source['text_sha256']
    new_sources = sources['new_primary_sources']['sources']
    for source in new_sources:
        if source['status'] == 'read':
            suffix = '.txt' if source['url'].endswith('.txt') else '.html'
            assert sha(ROOT / 'data/research/paper_readiness_20261022' / (source['id'] + suffix)) == source['sha256']
    before = {name: sha(REVIEW / name) for name in ('report.html', 'screening.html')}
    subprocess.run([sys.executable, str(REVIEW / 'build_review.py')], cwd=ROOT, check=True, timeout=10)
    assert before == {name: sha(REVIEW / name) for name in before}
    count = 0
    for name in before:
        parser = Links()
        parser.feed((REVIEW / name).read_text())
        for target in parser.links:
            url = urlsplit(target)
            if not url.scheme and url.path:
                assert (REVIEW / unquote(url.path)).exists(), target
                count += 1
    summary = dict(checkpoint_verified=True, overall_paper_ready=False, screening_runs=16,
                   independent_public_diagnoses=16, claims=len(claims['claims']),
                   reused_primary_source_count=len(sources['reused_primary_sources']),
                   new_primary_sources_read=sum(s['status'] == 'read' for s in new_sources),
                   offline_analysis_byte_identical=True, html_generation_byte_identical=True,
                   local_html_links=count, browser_visual_acceptance=False,
                   scope='screening evidence and review integrity only; does not establish A-class contribution')
    (a.output / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
