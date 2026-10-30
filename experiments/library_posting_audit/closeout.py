"""Check delivered hashes, links, source identities and bounded stage receipts."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        self.links.extend(v for k, v in attrs if k in ('href', 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--delivery', type=Path, required=True)
    args = parser.parse_args()
    stage, out = args.stage.resolve(), args.delivery.resolve()
    frozen = load(stage/'input/frozen.json')
    for name, h in frozen['source_code_sha256'].items():
        assert sha(ROOT/name) == h, name
    for name, h in frozen['raw_sha256'].items():
        assert sha(Path(name)) == h, name
    for name in ('sources.json', 'projections.json', 'frozen.json'):
        assert sha(out/name) == sha(stage/'input'/name)
    for name in ('analysis.json', 'verification.json'):
        assert sha(out/name) == sha(stage/name)
    verification = load(out/'verification.json')
    assert verification['passed'] and len(verification['tamper_rejected']) == 4
    assert verification['analysis_sha256'] == sha(out/'analysis.json')
    assert verification['verifier_sha256'] == sha(Path(__file__).with_name('verify.py'))
    for path in Path(__file__).parent.glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
    sources = load(out/'sources.json')
    for source in sources:
        assert sha(ROOT/source['path']) == source['sha256']
    probes = {p.parent.name: load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert set(probes) == {'prepare', 'analyze', 'verify', 'publish'}
    assert all(p['status'] == 'completed' and p['child_exit_code'] == 0 and not p['remaining_live_group_pids'] for p in probes.values())
    seconds = sum(p['wall_seconds'] for p in probes.values())
    artifact_peak = max(p['peak_sampled_artifact_bytes'] for p in probes.values())
    assert seconds < 300 and artifact_peak < 64*2**20 and shutil.disk_usage(stage).free > 8*2**30
    links = Links()
    links.feed((out/'report.html').read_text())
    allowed = {s['public_url'] for s in sources}
    for link in links.links:
        assert link in allowed if link.startswith('https://') else (out/link).is_file(), link
    markdown_links = 0
    for path in (out/'README.md', Path(__file__).with_name('README.md'), ROOT/'docs/tasks/posting_boundary_spec.md'):
        for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)', path.read_text()):
            link = match[1]
            if '://' not in link:
                assert (path.parent/link.split('#')[0]).is_file(), (path, link)
            markdown_links += 1
    assert not (out/'receipt.json').exists()
    snapshot = {p.name: sha(p) for p in out.iterdir() if p.is_file()}
    value = dict(passed=True, method_commit=frozen['method_commit'], method_sha256=frozen['source_code_sha256'],
        finalizer_sha256=sha(Path(__file__)), frozen_sha256=sha(stage/'input/frozen.json'),
        actual_commands={name: dict(command=p['command'], cwd=p['cwd'], child_exit_code=p['child_exit_code']) for name, p in probes.items()},
        monitored_seconds=seconds, monitored_stages=len(probes), peak_sampled_artifact_bytes=artifact_peak,
        peak_sampled_group_rss_bytes=max(p['peak_sampled_group_rss_bytes'] for p in probes.values()),
        budget=dict(monitored_seconds=300, artifact_bytes=64*2**20, min_free_bytes=8*2**30),
        monitor_scope='four analysis stages; preflight and finalization are separately read-only',
        source_files=len(sources), original_artifacts_hash_checked=len(frozen['raw_sha256']),
        tamper_rejected=verification['tamper_rejected'], static_HTML_links=len(links.links), markdown_links=markdown_links,
        browser_rendering_verified=False, new_networks=0, new_builds=0, downloads=0, GPU_execution=False,
        latency_predicted=False, hardware_calibrated=False, delivery_sha256=snapshot)
    with (out/'receipt.json').open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    for name, h in snapshot.items():
        assert sha(out/name) == h
    print(json.dumps(dict(passed=True, monitored_seconds=seconds, artifact_peak=artifact_peak,
                         original_artifacts=len(frozen['raw_sha256']), new_networks=0)))


if __name__ == '__main__':
    main()
