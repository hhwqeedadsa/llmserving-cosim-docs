"""Package verified offline route controls, render HTML, and hash-check a local archive."""
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
DOC = ROOT / 'docs/reviews/真实路由共选与网络偏斜受控分析_20261022.md'
SCRIPTS = ('prepare_route_controls.py', 'analyze_route_controls.py',
           'check_route_controls.py', 'build_route_controls_review.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('prepared', 'analysis', 'repeat_prepared', 'repeat_analysis',
                'verification', 'source_root', 'output', 'checkpoint'):
        parser.add_argument('--' + key.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--probe', type=Path, action='append', required=True,
                        help='Completed preparation, analysis and independent-check probe directories')
    a = parser.parse_args()
    out, checkpoint = a.output.resolve(), a.checkpoint.resolve()
    assert out.is_relative_to(ROOT) and checkpoint.is_relative_to(ROOT)
    assert not out.exists() and not checkpoint.exists()
    assert shutil.disk_usage(checkpoint.parent).free >= 8 * 2**30 + 64 * 2**20
    verification = json.loads(a.verification.read_text())
    assert verification['passed'] and verification['condition_window_rows_checked'] == 59392
    assert verification['destination_matrix_entries_checked'] == 950272
    assert verification['script_sha256'] == sha(ROOT / 'experiments/paper_readiness/check_route_controls.py')
    assert verification['summary_sha256'] == sha(a.analysis / 'summary.json')
    assert verification['windows_sha256'] == sha(a.analysis / 'windows.csv')
    assert verification['observed_index_sha256'] == sha(a.prepared / 'observed/index.json')
    for name in ('summary.json', 'windows.csv'):
        assert (a.analysis / name).read_bytes() == (a.repeat_analysis / name).read_bytes()
    for path in (a.prepared / 'observed').iterdir():
        assert path.read_bytes() == (a.repeat_prepared / 'observed' / path.name).read_bytes()
    probes = []
    for path in a.probe:
        report = json.loads((path / 'probe.json').read_text())
        assert report['status'] == 'completed' and report['child_exit_code'] == 0
        assert not report['remaining_live_group_pids']
        assert report['limits'] == dict(sampled_group_rss_bytes=2**30,
            per_process_address_bytes=2 * 2**30, artifact_bytes=64 * 2**20, wall_seconds=60.0)
        assert report['min_free_bytes'] == 8 * 2**30
        probes.append(dict(probe_directory=str(path), **report))
    assert len(probes) == 5
    out.mkdir(parents=True)
    for name in ('summary.json', 'execution.json'):
        shutil.copy2(a.analysis / name, out / ('analysis-' + name if name == 'execution.json' else name))
    shutil.copy2(a.verification, out / 'verification.json')
    for name in ('config.json', 'manifest.json', 'provenance.json', 'swap_summary.json'):
        shutil.copy2(a.prepared / 'truth' / name, out / name)
    shutil.copy2(a.prepared / 'observed/index.json', out / 'observed-index.json')
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True,
                          capture_output=True, text=True, timeout=10).stdout.strip()
    save(out / 'execution.json', dict(platform_base_commit=head,
        research_scripts={name: sha(ROOT / 'experiments/paper_readiness' / name) for name in SCRIPTS},
        cwd=str(ROOT), environment=dict(PYTHONPATH='python', PYTHONDONTWRITEBYTECODE='1'),
        commands=probes, network_executed=False, hardware_calibrated=False, goal_complete=False,
        read_only_network_requests=0, new_downloads=0, builds=0, dependency_changes=0))
    spec = importlib.util.spec_from_file_location('route_review_render',
        ROOT / 'docs/review_artifacts/paper_readiness_20261022/build_review.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HERE = out
    module.render(DOC, out / 'report.html', '真实路由共选与网络偏斜：八组受控分析')
    rendered, count = re.subn(r'<nav>.*?</nav>',
        '<nav><a href="summary.json">八组结果</a> · <a href="verification.json">独立核验</a> · '
        '<a href="execution.json">运行记录</a> · <a href="archive.json">本地归档</a></nav>',
        (out / 'report.html').read_text(), count=1)
    assert count == 1
    (out / 'report.html').write_text(rendered)

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paths = []

        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ('href', 'src'):
                    target = urlsplit(value)
                    if not target.scheme and target.path:
                        self.paths.append(out / unquote(target.path))

    links = Links()
    links.feed(rendered)
    for path in links.paths:
        if path != out / 'archive.json':
            assert path.is_file(), path
    files = {}

    def add_tree(source, prefix):
        for path in source.rglob('*'):
            if path.is_file():
                assert not path.is_symlink()
                files[prefix + '/' + str(path.relative_to(source))] = path

    for source, prefix in ((a.prepared, 'saved/prepared'), (a.analysis, 'saved/analysis'),
                           (a.repeat_prepared, 'saved/repeat-prepared'),
                           (a.repeat_analysis, 'saved/repeat-analysis')):
        add_tree(source, prefix)
    files['saved/verification.json'] = a.verification
    for i, probe in enumerate(a.probe):
        add_tree(probe, 'saved/probes/' + str(i))
    source_manifest = json.loads((a.prepared / 'truth/manifest.json').read_text())
    for item in source_manifest['files']:
        name = item['file']['path']
        path = a.source_root.resolve() / name
        assert path.is_relative_to(a.source_root.resolve()) and sha(path) == item['file']['sha256']
        files[name] = path
    for path in (DOC, ROOT / 'experiments/paper_readiness/README.md',
                 ROOT / 'experiments/paper_readiness/route_controls.json',
                 ROOT / 'experiments/paper_readiness/route_controls_protocol.md',
                 ROOT / 'docs/review_artifacts/paper_readiness_20261022/build_review.py',
                 *(ROOT / 'experiments/paper_readiness' / name for name in SCRIPTS)):
        files[str(path.relative_to(ROOT))] = path
    add_tree(out, str(out.relative_to(ROOT)))
    raw_bytes = sum(p.stat().st_size for p in files.values())
    assert raw_bytes <= 64 * 2**20
    checkpoint.mkdir()
    archive = checkpoint / 'checkpoint.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name, path in sorted(files.items()):
            tar.add(path, arcname=name, recursive=False)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getnames()
        assert len(members) == len(files) and set(members) == set(files)
        for name, path in files.items():
            assert hashlib.sha256(tar.extractfile(name).read()).hexdigest() == sha(path)
    save(out / 'archive.json', dict(path=str(archive.relative_to(ROOT)),
        sha256=sha(archive), bytes=archive.stat().st_size, unpacked_file_bytes=raw_bytes,
        verified_files=len(files), includes_self_archive_manifest=False,
        files={name: dict(bytes=path.stat().st_size, sha256=sha(path))
               for name, path in sorted(files.items())}))
    for path in links.paths:
        assert path.is_file(), path
    save(out / 'review-check.json', dict(local_html_links_checked=len(links.paths),
        archived_files_hash_verified=len(files), browser_visual_acceptance=False,
        network_executed=False, hardware_calibrated=False, goal_complete=False))
    assert sum(p.stat().st_size for p in out.iterdir()) <= 16 * 2**20
    print(json.dumps(dict(verified_files=len(files), archive_bytes=archive.stat().st_size,
                         html=str(out / 'report.html'), network_executed=False)))


if __name__ == '__main__':
    main()
