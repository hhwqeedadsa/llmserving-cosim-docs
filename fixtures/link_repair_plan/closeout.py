"""Record validated planning evidence, bounded resources and retained failures."""
import argparse
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urlsplit


def load(p):
    return json.loads(p.read_bytes())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.paths = []

    def handle_starttag(self, name, attrs):
        self.paths.extend(v for k, v in attrs if k in ('src', 'href') and v)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    a = p.parse_args()
    root, stage, out = Path(__file__).resolve().parents[2], a.stage.resolve(), Path(__file__).resolve().parent
    proof = load(out/'verification.json')
    assert proof['passed'] and proof['endpoint_destination_pairs'] == 256 and proof['explicit_prefixes'] == 16
    assert sha(out/'audit.py') == proof['verifier_sha256']
    assert sha(out/'plan.json') == proof['plan_sha256']
    assert sha(stage/'public/repair/topology.json') == proof['topology_sha256']
    assert sha(out/'summary.json') == sha(stage/'public/repair/summary.json')
    assert len(proof['tamper_checks']) == 4 and all(x['rejected'] for x in proof['tamper_checks'])
    probes = {p.parent.name: load(p) for p in (stage/'monitors').glob('*/probe.json')}
    assert len(probes) == 7
    failed = {n: x for n, x in probes.items() if x['child_exit_code'] != 0}
    assert set(failed) == {'unit', 'publish'}
    assert all(x['status'] == ('child_failed' if n in failed else 'completed') for n, x in probes.items())
    assert 'AssertionError: 0 != 2' in (stage/'monitors/unit/stderr.log').read_text()
    assert 'OpenBLAS error' in (stage/'monitors/publish/stderr.log').read_text()
    assert (stage/'publish-before-thread-limit.py').is_file()
    tests = {name: int(re.search(r'Ran (\d+) tests', (stage/'monitors'/name/'stderr.log').read_text())[1])
             for name in ('unit-corrected', 'regression')}
    assert tests == {'unit-corrected': 9, 'regression': 21}
    seconds = sum(x['wall_seconds'] for x in probes.values())
    artifact = max(x['peak_sampled_artifact_bytes'] for x in probes.values())
    assert seconds < 300 and artifact < 128*2**20 and shutil.disk_usage(stage).free > 8*2**30
    assert (stage/'dependency-check.txt').read_text().count('PASS: checked 13 dependency checkouts') == 1
    for command in load(stage/'public/commands.json'):
        assert command['exit_code'] == 0
    sources = load(stage/'sources/receipt.json')
    assert len(sources) == 2 and all(x['status'] == 'downloaded' for x in sources)
    for entry, number in zip(sources, (5286, 7490)):
        assert sha(stage/f'sources/rfc{number}.txt') == entry['sha256']
    links = 0
    docs = [out/'README.md', out/'report.html', root/'examples/scaleout_repair_plan/README.md',
            *[root/p for p in ('docs/tasks/link_repair_plan_spec.md', 'docs/tasks/current.md',
                'docs/user_guide.md', 'docs/architecture.md', 'docs/model_validation.md', 'docs/record_storage.md', 'backend/README.md')]]
    for doc in docs:
        if doc.suffix == '.html':
            parser = Links(); parser.feed(doc.read_text()); paths = parser.paths
        else:
            paths = re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)', doc.read_text())
        for link in paths:
            u = urlsplit(link)
            if u.scheme or u.netloc or not u.path:
                continue
            target = (doc.parent/unquote(u.path)).resolve()
            if target == out/'receipt.json':
                continue  # This file is produced only after the checks succeed.
            assert target.exists(), (str(doc), link)
            links += 1
    code = [root/'python/moe_ub_cosim/scaleout_repair_plan.py', root/'python/moe_ub_cosim/cli.py',
            root/'tests/unit/scaleout_repair_plan_test.py', *sorted(out.glob('*.py')),
            root/'examples/scaleout_repair_plan/clos-128.json', root/'docs/tasks/link_repair_plan_spec.md']
    receipt = dict(passed=True, network_executed=False, native_builds=0, GPU_execution=False, dependency_changes=0,
        actual_static_endpoints=128, source_destination_combinations=256, explicit_repair_prefixes=16,
        passed_unit_methods=sum(tests.values()), tests=tests, rejected_mutations=4,
        monitored_stages=len(probes), successful_stages=len(probes)-len(failed),
        monitored_seconds=seconds, peak_group_rss_bytes=max(x['peak_sampled_group_rss_bytes'] for x in probes.values()),
        peak_artifact_bytes=artifact, current_free_bytes=shutil.disk_usage(stage).free,
        failed_stages={n: dict(exit_code=x['child_exit_code'], stderr_sha256=sha(stage/'monitors'/n/'stderr.log')) for n, x in failed.items()},
        public_RFC_sources=sources, min_free_bytes=8*2**30, seconds_budget=300, artifact_budget_bytes=128*2**20,
        local_links_checked=links, figure_visually_inspected=True, browser_rendering_verified=False,
        sources_sha256={str(p.relative_to(root)): sha(p) for p in code},
        artifacts_sha256={p.name: sha(p) for p in out.iterdir() if p.suffix in ('.json', '.html', '.png')},
        boundaries=['static plan, no packet execution or timing benefit', 'single switch link, unit hop metrics',
                    'explicit repair prefixes still require native steering and wire overhead'])
    with (out/'receipt.json').open('x') as s:
        json.dump(receipt, s, indent=2); s.write('\n')
    print(json.dumps(dict(passed=True, monitored_seconds=seconds, passed_unit_methods=30,
                         endpoint_destination_pairs=256, local_links=links)))


if __name__ == '__main__':
    main()
