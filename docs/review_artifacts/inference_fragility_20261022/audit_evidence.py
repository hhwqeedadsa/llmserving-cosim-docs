"""Reaudit existing native outputs with public diagnostics; never run simulation."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        self.targets.extend(value for name, value in attrs if name in ('href', 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', type=Path, required=True)
    parser.add_argument('--build-provenance', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run = args.run_root.resolve()
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    commands = []
    results = []
    # Snapshot the list before writing any audit outputs.
    for index, path in enumerate(sorted(run.rglob('result.json'))):
        output = out / f'diagnosis-{index:03}.json'
        argv = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', 'rdma-diagnose',
                '--run', str(path.parent), '--output', str(output)]
        with (out / f'{index:03}.stdout').open('w') as stdout, (out / f'{index:03}.stderr').open('w') as stderr:
            completed = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=30)
        commands.append(dict(argv=argv, cwd=str(ROOT), exit_code=completed.returncode))
        save(out / 'commands.json', commands)
        if completed.returncode:
            raise RuntimeError(f'Existing result audit failed: {path}; preserve logs')
        result = json.loads(path.read_text())
        diagnosis = json.loads(output.read_text())
        assert diagnosis['passed'] == result['passed']
        results.append(dict(path=str(path.relative_to(run)), sha256=sha(path),
                            run_passed=result['passed'], diagnosis_sha256=sha(output),
                            recovery=diagnosis['recovery']['available']))

    provenance = json.loads(args.build_provenance.read_text())
    for name, expected in provenance['sources'].items():
        assert sha(ROOT / name) == expected, f'Build source changed: {name}'
    experiment = json.loads((run / 'experiment/experiment.json').read_text())
    for name, expected in experiment['sources'].items():
        assert sha(ROOT / name) == expected, f'Execution source changed: {name}'

    # Exact offline repeat and committed review artifacts must agree.
    for name in ('results.md', 'summary.json', 'report.html'):
        expected = sha(run / 'analysis-accepted' / name)
        assert sha(run / 'reanalysis-accepted' / name) == expected
        assert sha(HERE / name) == expected
    assert sha(ROOT / 'experiments/inference_fragility/results.md') == sha(HERE / 'results.md')

    before = {name: sha(HERE / name) for name in ('architecture.html', 'formal.html')}
    completed = subprocess.run([sys.executable, str(HERE / 'build_review.py')], cwd=ROOT, timeout=10)
    assert completed.returncode == 0
    assert before == {name: sha(HERE / name) for name in before}
    checked = 0
    for name in ('architecture.html', 'formal.html', 'report.html'):
        links = Links()
        links.feed((HERE / name).read_text())
        for target in links.targets:
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            assert (HERE / unquote(parsed.path)).exists(), f'Missing local link in {name}: {target}'
            checked += 1
    summary = dict(passed=True, result_count=len(results),
                   failed_runs_preserved=sum(not item['run_passed'] for item in results),
                   recovery_result_count=sum(item['recovery'] for item in results),
                   build_source_count=len(provenance['sources']),
                   experiment_source_count=len(experiment['sources']), local_html_links=checked,
                   offline_analysis_byte_identical=True, html_renderer_byte_identical=True,
                   browser_visual_acceptance=False, results=results,
                   scope='existing-output public diagnosis and evidence verification; no new simulations')
    save(out / 'verification.json', summary)
    print(json.dumps({key: value for key, value in summary.items() if key != 'results'}))


if __name__ == '__main__':
    main()
