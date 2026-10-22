"""Pin and read a bounded modern DeepEP source subset, without installing it.

This is research input collection, not a platform dependency upgrade.
Each invocation is bounded to 60 seconds; both phases together to 120 seconds
and 16 MiB of response bytes. Failed collections are retained and not retried.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import resource
import re
import sys
import time
from urllib.request import Request, urlopen

API = 'https://api.github.com/repos/deepseek-ai/DeepEP/git/'
LIMIT = 2 * 2**20


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'MoE-modern-source-audit',
                                      'Accept': 'application/vnd.github+json'}), timeout=20) as response:
        raw = response.read(LIMIT + 1)
        assert response.status == 200 and len(raw) <= LIMIT
    return raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--phase', choices=('tree', 'files'), required=True)
    p.add_argument('--paths', nargs='*')
    p.add_argument('--batch', default='first', help='Unique collection name; never retries or overwrites a batch')
    p.add_argument('--commit', help='Full immutable commit for tree phase; omit only for initial discovery')
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (2*2**30, 2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    begin = time.monotonic()
    out = a.output.resolve()
    if a.phase == 'tree':
        assert not a.paths
        assert a.commit is None or re.fullmatch('[0-9a-f]{40}', a.commit)
        out.mkdir(parents=True, exist_ok=False)
        record = dict(accessed_utc=datetime.now(timezone.utc).isoformat(), requests=[],
                      argv=sys.argv, cwd=str(Path.cwd()),
                      purpose='pin current public source for comparison; no hardware compatibility assumed')
        try:
            ref_url = API + ('commits/'+a.commit if a.commit else 'ref/heads/main')
            raw = fetch(ref_url)
            record['requests'].append(dict(url=ref_url, bytes=len(raw), sha256=digest(raw)))
            (out/'ref.json').write_bytes(raw)
            ref = json.loads(raw)
            if a.commit:
                assert ref['sha'] == a.commit
                commit = a.commit
            else:
                assert ref['object']['type'] == 'commit'
                commit = ref['object']['sha']
            assert len(commit) == 40 and all(c in '0123456789abcdef' for c in commit)
            record['commit'] = commit
            tree_url = API+'trees/'+commit+'?recursive=1'
            raw = fetch(tree_url)
            record['requests'].append(dict(url=tree_url, bytes=len(raw), sha256=digest(raw)))
            tree = json.loads(raw)
            assert tree['sha'] == commit and not tree['truncated']
            (out/'tree.json').write_bytes(raw)
            record['status'] = 'read'
            for item in tree['tree']:
                if item['type'] == 'blob':
                    print(item['path'], item.get('size'))
        except Exception as error:
            record.update(status='unavailable', reason=str(error))
        record['elapsed_seconds'] = time.monotonic()-begin
        save(out/'tree-source.json', record)
        assert record['status'] == 'read', 'Preserve failure; no automatic retry'
        return

    assert a.commit is None and a.paths and len(a.paths) <= 6 and len(set(a.paths)) == len(a.paths)
    assert re.fullmatch('[a-z0-9_]{1,32}', a.batch)
    destination = out/('file-sources.json' if a.batch == 'first' else 'file-sources-'+a.batch+'.json')
    assert not destination.exists()
    previous = json.loads((out/'tree-source.json').read_text())
    assert previous['status'] == 'read'
    raw_tree = (out/'tree.json').read_bytes()
    assert digest(raw_tree) == previous['requests'][1]['sha256']
    tree = json.loads(raw_tree)
    entries = {i['path']: i for i in tree['tree'] if i['type'] == 'blob'}
    collections = [json.loads(p.read_text()) for p in sorted(out.glob('file-sources*.json'))]
    assert all(c['commit'] == previous['commit'] for c in collections)
    prior_records = [r for c in collections for r in c['sources']]
    prior_bytes = sum(r.get('response_bytes', 0) for r in prior_records)
    prior_elapsed = sum(c.get('batch_elapsed_seconds', c['elapsed_seconds']-previous['elapsed_seconds']) for c in collections)
    assert len(prior_records)+len(a.paths) <= 12
    # Reserve a full bounded response for every request before starting.
    assert sum(r['bytes'] for r in previous['requests']) + prior_bytes + len(a.paths)*LIMIT <= 16*2**20
    assert previous['elapsed_seconds']+prior_elapsed+40 <= 120
    for name in a.paths:
        path = PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert name in entries and entries[name]['size'] <= LIMIT
        assert name not in {r['path'] for r in prior_records} and not (out/'sources'/name).exists()

    def collect(name):
        item = entries[name]
        url = API+'blobs/'+item['sha']
        record = dict(path=name, commit=previous['commit'], git_blob_sha=item['sha'], url=url,
                      public_url='https://github.com/deepseek-ai/DeepEP/blob/'+previous['commit']+'/'+name)
        try:
            response = fetch(url)
            record['response_bytes'] = len(response)
            blob = json.loads(response)
            assert blob['sha'] == item['sha'] and blob['encoding'] == 'base64'
            raw = base64.b64decode(blob['content'], validate=False)
            assert len(raw) == item['size'] == blob['size']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == item['sha']
            dest = out/'sources'/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            record.update(status='read', bytes=len(raw), sha256=digest(raw))
        except Exception as error:
            record.update(status='unavailable', reason=str(error))
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(collect, a.paths))
    batch_elapsed = time.monotonic()-begin
    response_bytes = sum(r['bytes'] for r in previous['requests']) + prior_bytes + sum(r.get('response_bytes', 0) for r in records)
    elapsed = previous['elapsed_seconds']+prior_elapsed+batch_elapsed
    result = dict(commit=previous['commit'], sources=records, response_bytes=response_bytes,
                  elapsed_seconds=elapsed, batch_elapsed_seconds=batch_elapsed,
                  argv=sys.argv, cwd=str(Path.cwd()),
                  scope='read-only fixed sources; no kernel execution or dependency update')
    save(destination, result)
    assert response_bytes <= 16*2**20 and elapsed <= 120
    assert all(r['status'] == 'read' for r in records), 'Preserve failure; no automatic retry'
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
