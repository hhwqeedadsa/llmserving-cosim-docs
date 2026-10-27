"""Bounded public source intake; no execution of fetched code or retry loops."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import urllib.error


def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def acquire(output, requests):
    output.mkdir(parents=True, exist_ok=True)
    receipt = output/'acquisition.json'
    rows = json.loads(receipt.read_text()) if receipt.exists() else []
    assert len(rows)+len(requests) <= 8
    assert sum(r.get('bytes', 0) for r in rows) < 16*2**20
    names = {r['name'] for r in rows}
    for row in requests:
        assert set(row) == {'name', 'url'} and row['name'].replace('-', '').isalnum()
        assert row['name'] not in names and row['url'].startswith('https://')
        names.add(row['name'])
    offset = len(rows)
    rows.extend(dict(**r, state='reserved') for r in requests); save(receipt, rows)
    def request(index, row):
        start = time.monotonic(); response = None
        try:
            response = urllib.request.urlopen(urllib.request.Request(row['url'], headers={
                'User-Agent': 'MoE-ScaleOut-source-audit/1', 'Accept': '*/*'}), timeout=12)
            raw = response.read(4*2**20+1)
            assert len(raw) <= 4*2**20, 'response exceeds registered limit'
            (output/(row['name']+'.body')).write_bytes(raw)
            value = dict(row, state='completed', status=response.status,
                final_url=response.url, content_type=response.headers.get('Content-Type'),
                bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        except Exception as error:
            value = dict(row, state='failed', error_type=type(error).__name__,
                status=getattr(error, 'code', None), bytes=0)
        finally:
            if response is not None: response.close()
        value['wall_seconds'] = time.monotonic()-start
        return index, value
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(request, offset+i, row) for i, row in enumerate(requests)]
        for future in as_completed(futures):
            index, value = future.result(); rows[index] = value; save(receipt, rows)
            print(value['name'], value['state'], value.get('status'), value['bytes'], flush=True)
    assert sum(r.get('bytes', 0) for r in rows) <= 16*2**20


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('action', choices=('query', 'fetch'))
    p.add_argument('--output', type=Path, required=True); p.add_argument('--requests', type=Path)
    a = p.parse_args()
    if a.action == 'query':
        requests = [dict(name='github-parsimon', url='https://api.github.com/search/repositories?q=parsimon+network&per_page=3'),
            dict(name='github-mimicnet', url='https://api.github.com/search/repositories?q=mimicnet&per_page=3'),
            dict(name='openalex-parsimon', url='https://api.openalex.org/works?search=Parsimon%20network&per-page=3'),
            dict(name='openalex-mimicnet', url='https://api.openalex.org/works?search=MimicNet&per-page=3')]
    else: requests = json.loads(a.requests.read_text())
    acquire(a.output.resolve(), requests)
