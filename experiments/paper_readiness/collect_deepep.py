"""Read a bounded, fixed DeepEP source subset; never install or execute it."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

COMMIT = '9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'
API = 'https://api.github.com/repos/deepseek-ai/DeepEP/git/'
LIMIT = 2 * 1024**2


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def fetch(url):
    with urlopen(Request(url, headers={'User-Agent': 'MoE-semantic-source-audit',
                                      'Accept': 'application/vnd.github+json'}), timeout=20) as response:
        data = response.read(LIMIT + 1)
        assert response.status == 200 and len(data) <= LIMIT, 'Source response exceeded declared bound'
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--phase', choices=('tree', 'files'), required=True)
    p.add_argument('--paths', nargs='*')
    a = p.parse_args()
    out = a.output.resolve()
    if a.phase == 'tree':
        out.mkdir(exist_ok=False, parents=True)
        url = API + 'trees/' + COMMIT + '?recursive=1'
        record = dict(url=url, commit=COMMIT, accessed_utc=datetime.now(timezone.utc).isoformat())
        try:
            data = fetch(url)
            tree = json.loads(data)
            assert not tree['truncated'] and tree['sha'] == COMMIT
            (out / 'tree.json').write_bytes(data)
            record.update(status='read', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
            for item in tree['tree']:
                if item['type'] == 'blob' and (item['path'].startswith('csrc/') or item['path'].startswith('deep_ep/')):
                    print(item['path'], item.get('size'))
        except Exception as error:
            record.update(status='unavailable', reason=str(error))
        save(out / 'tree-source.json', record)
        assert record['status'] == 'read', 'Preserve unavailable source; no automatic retry'
        return
    assert a.paths and len(a.paths) <= 7
    assert not (out / 'file-sources.json').exists(), 'Do not overwrite a collection attempt'
    tree = json.loads((out / 'tree.json').read_text())
    entries = {i['path']: i for i in tree['tree'] if i['type'] == 'blob'}
    assert len(set(a.paths)) == len(a.paths) and all(n in entries for n in a.paths)

    def collect(name):
        item = entries[name]
        url = API + 'blobs/' + item['sha']
        record = dict(path=name, commit=COMMIT, git_blob_sha=item['sha'], url=url,
                      public_url='https://github.com/deepseek-ai/DeepEP/blob/' + COMMIT + '/' + name,
                      accessed_utc=datetime.now(timezone.utc).isoformat())
        try:
            response = fetch(url)
            blob = json.loads(response)
            assert blob['encoding'] == 'base64' and blob['sha'] == item['sha']
            raw = base64.b64decode(blob['content'])
            assert len(raw) == item['size'] == blob['size']
            assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == item['sha']
            dest = out / 'sources' / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
            record.update(status='read', bytes=len(raw), response_bytes=len(response), sha256=hashlib.sha256(raw).hexdigest())
        except Exception as error:
            record.update(status='unavailable', reason=str(error))
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(collect, a.paths))
    total = len((out / 'tree.json').read_bytes()) + sum(r.get('response_bytes', 0) for r in records)
    assert total <= 16 * 1024**2
    save(out / 'file-sources.json', dict(commit=COMMIT, sources=records, response_bytes=total,
                                        scope='static fixed-source audit only; no library execution, dependency change or hardware measurement'))
    print(json.dumps([{k: r[k] for k in ('path', 'status', 'bytes', 'reason') if k in r} for r in records]))


if __name__ == '__main__':
    main()
