"""Bounded, read-only official documentation and fixed-source verification."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import resource
import shutil
from urllib.request import Request, urlopen


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip -= 1

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2 * 2**30, 2 * 2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    assert shutil.disk_usage(a.output.parent).free >= 8 * 2**30 + 64 * 2**20
    a.output.mkdir(exist_ok=False)
    base = a.repo / 'data/research/deepep_modern_20261022'
    meta = json.loads((a.repo / 'docs/review_artifacts/deepep_modern_20261022/sources.json').read_text())
    tree = json.loads((base / 'tree.json').read_text())
    fixed = []
    wanted = {
        'deep_ep/include/deep_ep/comm/barrier.cuh': [(30, 48)],
        'deep_ep/include/deep_ep/comm/handle.cuh': [(178, 200)],
        'deep_ep/include/deep_ep/impls/ep/combine.cuh': [(112, 175), (239, 255)],
    }
    for path, ranges in wanted.items():
        row = next(r for r in meta['sources'] if r['path'] == path)
        raw = (base / 'sources' / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha']
        assert any(e['path'] == path and e['sha'] == row['git_blob_sha'] for e in tree['tree'])
        lines = raw.decode().splitlines()
        fixed.append({**{k: row[k] for k in ('path', 'commit', 'git_blob_sha', 'sha256', 'public_url')},
                      'excerpts': [dict(first=x, last=y, text='\n'.join(lines[x-1:y])) for x, y in ranges]})
    urls = [
        ('ptx', 'https://docs.nvidia.com/cuda/parallel-thread-execution/index.html'),
        ('runtime_errors', 'https://docs.nvidia.com/cuda/archive/12.8.0/cuda-runtime-api/group__CUDART__TYPES.html'),
    ]

    def fetch(item):
        name, url = item
        row = dict(id=name, url=url, accessed_utc=datetime.now(timezone.utc).isoformat())
        try:
            with urlopen(Request(url, headers={'User-Agent': 'MoE-state-survival-review'}), timeout=20) as r:
                raw = r.read(8 * 2**20 + 1)
                row.update(status_code=r.status, resolved_url=r.url)
            assert len(raw) <= 8 * 2**20, 'over 8 MiB; no retry'
            (a.output / (name + '.html')).write_bytes(raw)
            parser = Plain(); parser.feed(raw.decode())
            plain = '\n'.join(' '.join(s.split()) for s in ''.join(parser.parts).splitlines() if s.strip())
            (a.output / (name + '.txt')).write_text(plain)
            row.update(status='read', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        except Exception as exc:
            row.update(status='unavailable', error=str(exc))
        return row

    with ThreadPoolExecutor(max_workers=2) as pool:
        remote = list(pool.map(fetch, urls))
    assert sum(r.get('bytes', 0) for r in remote) <= 12 * 2**20
    summary = dict(fixed=fixed, remote=remote, gpu_executed=False,
                   error_mapping_of_specific_kernel='not established by static documentation')
    (a.output / 'sources.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    assert sum(x.stat().st_size for x in a.output.iterdir()) < 64 * 2**20
    print(json.dumps(remote))


if __name__ == '__main__': main()
