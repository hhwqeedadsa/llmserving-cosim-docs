"""Offline repeat, archive and relocation checks for the fixed-source GIN study."""
import argparse
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import resource
import tarfile
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_archive(review):
    record = json.loads((review / 'archive.json').read_text())
    path = ROOT / record['path']
    assert sha(path) == record['sha256']
    checked = 0
    with tarfile.open(path, 'r:gz') as archive:
        assert len(archive.getnames()) == len(set(archive.getnames()))
        assert set(archive.getnames()) == set(record['files'])
        for name, expected in record['files'].items():
            raw = archive.extractfile(name).read()
            assert len(raw) == expected['bytes']
            assert hashlib.sha256(raw).hexdigest() == expected['sha256']
            assert sha(ROOT / name) == expected['sha256'], name
            checked += 1
    return dict(archive=record['path'],sha256=record['sha256'],verified_files=checked)


def check_links(review):
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paths = []

        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in ('href', 'src'):
                    url = urlsplit(value)
                    if not url.scheme and url.path:
                        self.paths.append(review / unquote(url.path))

    links = Links()
    links.feed((review / 'report.html').read_text())
    for path in links.paths:
        assert path.exists(), path
    return len(links.paths)


def main():
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    started = time.monotonic()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review', required=True, type=Path)
    p.add_argument('--repeat-routes', required=True, type=Path)
    p.add_argument('--relocated', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    assert not a.output.exists()
    repeat = {}
    for name in ('summary.json', 'provenance.json', 'windows.csv'):
        original = a.review / ('routes-' + name)
        assert original.read_bytes() == (a.repeat_routes / name).read_bytes(), name
        repeat[name] = sha(original)
    relocated = {}
    for name in ('sources.json', 'mapping.json', 'collection.json',
                 'routes-summary.json', 'routes-provenance.json', 'routes-windows.csv'):
        assert (a.review / name).read_bytes() == (a.relocated / name).read_bytes(), name
        relocated[name] = sha(a.review / name)
    archives = [check_archive(path) for path in (a.review, a.relocated)]
    link_counts = [check_links(path) for path in (a.review, a.relocated)]
    assert time.monotonic() - started < 60
    usage = resource.getrusage(resource.RUSAGE_SELF)
    assert usage.ru_maxrss <= 2**20
    result = dict(status='passed', deterministic_route_outputs=repeat,
                  relocation_stable_outputs=relocated, archives=archives,
                  local_html_link_counts=link_counts,
                  wall_seconds=time.monotonic()-started, peak_rss_kib=usage.ru_maxrss,
                  execution_metadata_expected_to_differ=True,
                  browser_visual_acceptance=False, network_execution=False,
                  hardware_calibrated=False, paper_goal_complete=False)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
