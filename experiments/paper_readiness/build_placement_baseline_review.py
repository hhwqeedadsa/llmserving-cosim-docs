"""Render and archive the verified Prefill reference study without rerunning it."""
import argparse
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs/reviews/Prefill放置强基线与通信目标边界_20261022.md'
NAMES = ('collect_placement_reference.py', 'prepare_placement_baselines.py', 'plan_placement_baselines.py',
         'evaluate_placement_baselines.py', 'check_placement_baselines.py', 'build_placement_baseline_review.py')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, v): p.write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'verified', 'reference_sources', 'source_root', 'output', 'checkpoint'):
        p.add_argument('--' + name.replace('_', '-'), type=Path, required=True)
    p.add_argument('--source-probe', type=Path, action='append', default=[])
    a = p.parse_args(); run, out, target = (v.resolve() for v in (a.run, a.output, a.checkpoint))
    assert out.is_relative_to(ROOT) and target.is_relative_to(ROOT)
    assert not out.exists() and not target.exists() and shutil.disk_usage(target.parent).free >= 8 * 2**30 + 64 * 2**20
    verification = json.loads((a.verified / 'verification.json').read_text())
    assert verification['passed'] and verification['condition_window_rows_checked'] == 22272
    assert verification['destination_matrix_entries_checked'] == 356352
    assert verification['summary_sha256'] == sha(run / 'evaluated/summary.json')
    assert verification['csv_sha256'] == sha(run / 'evaluated/windows.csv')
    assert (run / 'planned/placements.json').read_bytes() == (run / 'repeat-planned/placements.json').read_bytes()
    probes = []
    for path in [*sorted(run.glob('*-probe')), *a.source_probe]:
        # A caller may place the running packaging probe under --run. Do not
        # read or archive its still-changing samples or self-referential output.
        if not (path / 'probe.json').exists():
            assert path.name == 'review-probe'
            continue
        report = json.loads((path / 'probe.json').read_text())
        assert report['status'] in ('completed', 'child_failed')
        assert not report['remaining_live_group_pids']
        probes.append(dict(probe_directory=str(path), **report))
    for name in ('prepare-probe', 'plan-probe', 'evaluate-probe', 'repeat-plan-probe'):
        report = json.loads((run / name / 'probe.json').read_text())
        assert report['status'] == 'completed' and report['child_exit_code'] == 0
    out.mkdir(parents=True)
    shutil.copy2(run / 'evaluated/summary.json', out / 'summary.json')
    for name in ('verification.json', 'sources.json', 'pair_counterexample.json'):
        shutil.copy2(a.verified / name, out / name)
    for name in ('manifest.json', 'provenance.json'):
        shutil.copy2(run / 'prepared/truth' / name, out / name)
    shutil.copy2(run / 'planned/placements.json', out / 'placements.json')
    save(out / 'execution.json', dict(cwd=str(ROOT), platform_base_commit=subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True, timeout=10).stdout.strip(),
        commands=probes, research_scripts={name: sha(ROOT / 'experiments/paper_readiness' / name) for name in NAMES},
        failed_checker=dict(reason='Initial expected P/Q counts assigned to the opposite parity family; hand enumeration corrected labels only',
            preserved_in_run=True, baseline_preparation_planning_evaluation_rerun=False) if any(r['status'] == 'child_failed' for r in probes) else None,
        downloaded_code_executed=False, gpu_executed=False, network_executed=False, hardware_calibrated=False, goal_complete=False))
    spec = importlib.util.spec_from_file_location('prefill_render', ROOT / 'docs/review_artifacts/paper_readiness_20261022/build_review.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); module.HERE = out
    module.render(DOC, out / 'report.html', 'Prefill放置强基线与通信目标边界')
    html, n = re.subn(r'<nav>.*?</nav>', '<nav><a href="summary.json">三组结果</a> · '
        '<a href="sources.json">原文与源码</a> · <a href="verification.json">独立核验</a> · '
        '<a href="pair_counterexample.json">两两统计反例</a> · <a href="archive.json">本地归档</a></nav>',
        (out / 'report.html').read_text(), count=1)
    assert n == 1; (out / 'report.html').write_text(html)

    class Links(HTMLParser):
        def __init__(self): super().__init__(); self.paths = []
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ('href', 'src'):
                    u = urlsplit(value)
                    if not u.scheme and u.path: self.paths.append(out / unquote(u.path))

    links = Links(); links.feed(html)
    for path in links.paths:
        if path != out / 'archive.json': assert path.is_file(), path
    files = {}
    def add_tree(source, prefix):
        for path in source.rglob('*'):
            if path.is_file() and 'review-probe' not in path.relative_to(source).parts:
                assert not path.is_symlink()
                files[prefix + '/' + str(path.relative_to(source))] = path
    add_tree(run, 'saved_run')
    for index, path in enumerate(a.source_probe): add_tree(path, 'source_probes/' + str(index))
    add_tree(a.reference_sources.resolve(), str(a.reference_sources.resolve().relative_to(ROOT)))
    manifest = json.loads((run / 'prepared/truth/manifest.json').read_text())
    for item in manifest['files']:
        name = item['file']['path']; path = a.source_root.resolve() / name
        assert path.is_relative_to(a.source_root.resolve()) and sha(path) == item['file']['sha256']
        files[name] = path
    source_audit = json.loads((a.verified / 'sources.json').read_text())
    for name in (source_audit['paper']['local_snapshot'], source_audit['paper']['text_snapshot']): files[name] = ROOT / name
    for path in (DOC, ROOT / 'experiments/paper_readiness/README.md',
                 ROOT / 'experiments/paper_readiness/placement_baselines_protocol.md',
                 ROOT / 'docs/review_artifacts/paper_readiness_20261022/build_review.py',
                 ROOT / 'docs/review_artifacts/paper_readiness_20261022/sources.json',
                 *(ROOT / 'experiments/paper_readiness' / name for name in NAMES)):
        files[str(path.relative_to(ROOT))] = path
    add_tree(out, str(out.relative_to(ROOT)))
    total = sum(path.stat().st_size for path in files.values()); assert total <= 64 * 2**20
    target.mkdir(); archive = target / 'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, path in sorted(files.items()): tar.add(path, arcname=name, recursive=False)
    with tarfile.open(archive, 'r:gz') as tar:
        assert len(tar.getnames()) == len(files) and set(tar.getnames()) == set(files)
        for name, path in files.items(): assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == sha(path)
    save(out / 'archive.json', dict(path=str(archive.relative_to(ROOT)), bytes=archive.stat().st_size,
        sha256=sha(archive), verified_files=len(files), unpacked_file_bytes=total,
        files={name: dict(bytes=path.stat().st_size, sha256=sha(path)) for name, path in sorted(files.items())},
        includes_self_archive_manifest=False, includes_packaging_probe=False))
    for path in links.paths: assert path.is_file(), path
    save(out / 'review-check.json', dict(html_local_links_checked=len(links.paths), archived_files_hash_verified=len(files),
        browser_visual_acceptance=False, network_executed=False, hardware_calibrated=False, goal_complete=False))
    assert sum(path.stat().st_size for path in out.iterdir()) < 16 * 2**20
    print(json.dumps(dict(verified_files=len(files), archive_bytes=archive.stat().st_size, html=str(out / 'report.html'))))


if __name__ == '__main__': main()
