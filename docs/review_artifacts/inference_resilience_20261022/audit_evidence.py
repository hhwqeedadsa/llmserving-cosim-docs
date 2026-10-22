"""Re-diagnose accepted final-schema runs; verify offline artifacts and sources."""
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
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
        self.targets.extend(value for key, value in attrs if key in ('src', 'href'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-root', type=Path, required=True)
    p.add_argument('--build-provenance', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reuse-from', type=Path, help='Reuse successful public diagnoses from a preserved audit attempt')
    p.add_argument('--analysis', default='analysis-accepted')
    p.add_argument('--reanalysis', default='reanalysis-accepted')
    args = p.parse_args()
    run, out = args.run_root.resolve(), args.output.resolve()
    out.mkdir(exist_ok=False)
    # Never apply current per-source-history schema to early global-LRU records.
    selected = ('fixed-accepted', 'adaptive-accepted', 'resilience-local-gates',
                'public-delivery', 'sweep-terminal-gates', 'research')
    paths = sorted({q for name in selected for q in (run / name).rglob('result.json')})
    commands, results = [], []
    reused = {}
    if args.reuse_from:
        for command in json.loads((args.reuse_from / 'commands.json').read_text()):
            assert command['exit_code'] == 0
            if command.get('mode') == 'reuse_existing_public_diagnosis':
                continue
            old = command['argv']
            reused[old[old.index('--run') + 1]] = Path(old[old.index('--output') + 1])
        # The completed verification index supplies paths even for earlier reuse.
        index = args.reuse_from / 'verification.json'
        if index.exists():
            previous_results = json.loads(index.read_text())['results']
            for i, record in enumerate(previous_results):
                reused[str(run / record['path'].removesuffix('/result.json'))] = args.reuse_from / f'diagnosis-{i:03}.json'
                assert sha(run / record['path']) == record['sha256']
    for i, path in enumerate(paths):
        dest = out / f'diagnosis-{i:03}.json'
        argv = [sys.executable, '-m', 'moe_ub_cosim', 'scaleout', 'rdma-diagnose',
                '--run', str(path.parent), '--output', str(dest)]
        previous = reused.get(str(path.parent))
        if previous:
            shutil.copyfile(previous, dest)
            exit_code = 0
            record = dict(mode='reuse_existing_public_diagnosis', reused_from=str(previous), sha256=sha(previous))
        else:
            with (out / f'{i:03}.stdout').open('w') as f, (out / f'{i:03}.stderr').open('w') as e:
                completed = subprocess.run(argv, cwd=ROOT, stdout=f, stderr=e, timeout=30)
            exit_code = completed.returncode
            record = dict(mode='execute_public_diagnosis', argv=argv)
        commands.append(dict(cwd=str(ROOT), exit_code=exit_code, **record))
        save(out / 'commands.json', commands)
        if exit_code:
            raise RuntimeError(f'Preserve failed re-diagnosis: {path}')
        actual, diagnosis = json.loads(path.read_text()), json.loads(dest.read_text())
        assert actual['passed'] == diagnosis['passed']
        history = actual.get('recovery', {}).get('history')
        reconstructed = False
        if history is not None:
            if actual['recovery']['complete']:
                assert diagnosis['recovery']['rtt']['history_reconstructed']
                reconstructed = True
            else:
                assert diagnosis['recovery']['rtt']['reason'] == 'capture_truncated'
        results.append(dict(path=str(path.relative_to(run)), sha256=sha(path),
                            run_passed=actual['passed'], history_enabled=history is not None,
                            history_reconstructed=reconstructed))
    provenance = json.loads(args.build_provenance.read_text())
    for name, checksum in provenance['sources'].items():
        assert sha(ROOT / name) == checksum, name
    experiment = json.loads((run / 'research/experiment.json').read_text())
    for name, checksum in experiment['source_hashes'].items():
        assert sha(ROOT / name) == checksum, name
    assert sha(ROOT / 'experiments/inference_resilience/capture.py') == json.loads((run / 'research/captures/capture.json').read_text())['capture_script_sha256']
    for name in ('summary.json', 'results.md', 'report.html'):
        checksum = sha(run / args.analysis / name)
        assert sha(run / args.reanalysis / name) == checksum
        assert sha(HERE / name) == checksum
    before = {name: sha(HERE / name) for name in ('architecture.html', 'formal.html')}
    subprocess.run([sys.executable, str(HERE / 'build_review.py')], cwd=ROOT, check=True, timeout=10)
    assert before == {name: sha(HERE / name) for name in before}
    checked = 0
    for name in ('architecture.html', 'formal.html', 'report.html'):
        parser = Links()
        parser.feed((HERE / name).read_text())
        for target in parser.targets:
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            assert (HERE / unquote(parsed.path)).exists(), (name, target)
            checked += 1
    summary = dict(passed=True, result_count=len(results), failed_runs_preserved=sum(not r['run_passed'] for r in results),
                   history_result_count=sum(r['history_reconstructed'] for r in results),
                   history_capture_incomplete=sum(r['history_enabled'] and not r['history_reconstructed'] for r in results),
                   reused_public_diagnoses=len(reused),
                   build_source_count=len(provenance['sources']), experiment_source_count=len(experiment['source_hashes']),
                   offline_analysis_byte_identical=True, html_renderer_byte_identical=True,
                   local_html_links=checked, browser_visual_acceptance=False,
                   selected_final_schema_roots=selected, results=results,
                   scope='offline public diagnosis; no new simulation; early superseded records excluded')
    save(out / 'verification.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'results'}))


if __name__ == '__main__':
    main()
