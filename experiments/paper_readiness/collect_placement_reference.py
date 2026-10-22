"""Read fixed primary placement source; never install or execute downloaded code."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

REPO = 'zhongkaiyu/moe_exp_placement'
API = 'https://api.github.com/repos/' + REPO


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('identity', 'files'), required=True)
    parser.add_argument('--commit', help='Required fixed commit for identity reproduction')
    parser.add_argument('--paths', nargs='*', default=[])
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    out = a.output.resolve()
    if a.phase == 'identity':
        assert not out.exists() and out.parent.is_dir()
        out.mkdir()
        record = dict(repository=REPO, requests=[], downloaded_code_executed=False)
    else:
        record = json.loads((out / 'requests.json').read_text())
        assert record['repository'] == REPO and 1 <= len(a.paths) <= 2
        assert len(a.paths) == len(set(a.paths))
        assert all(row['status'] == 'read' for row in record['requests']), 'Previous request failed; preserve and stop'
    assert len(record['requests']) < 10

    def get(url, target):
        assert not target.exists()
        assert len(record['requests']) < 10 and all(row['url'] != url for row in record['requests'])
        assert sum(r.get('response_bytes', 0) for r in record['requests']) < 14 * 2**20
        assert sum(r.get('wall_seconds', 0) for r in record['requests']) < 100
        start = time.monotonic()
        row = dict(url=url, target=str(target.relative_to(out)),
                   accessed_utc=datetime.now(timezone.utc).isoformat())
        record['requests'].append(row)
        try:
            with urlopen(Request(url, headers={'User-Agent': 'MoE-placement-source-review',
                                               'Accept': 'application/vnd.github+json'}), timeout=20) as response:
                content = response.read(2 * 2**20 + 1)
                row['http_status'] = response.status
                row['response_bytes'] = len(content)
                assert len(content) <= 2 * 2**20
            target.write_bytes(content)
            row.update(status='read', sha256=hashlib.sha256(content).hexdigest())
            return json.loads(content)
        except Exception as e:
            row.update(status='failed', error=type(e).__name__)
            raise
        finally:
            row['wall_seconds'] = time.monotonic() - start
            save(out / 'requests.json', record)

    if a.phase == 'identity':
        commit = get(API + '/commits/' + (a.commit or 'HEAD'), out / 'commit.json')
        if a.commit:
            assert commit['sha'] == a.commit
        tree = get(API + '/git/trees/' + commit['commit']['tree']['sha'] + '?recursive=1', out / 'tree.json')
        assert not tree.get('truncated', False) and tree['sha'] == commit['commit']['tree']['sha']
        save(out / 'identity.json', dict(repository=REPO, commit=commit['sha'], tree=tree['sha']))
        print(json.dumps(dict(commit=commit['sha'], file_count=sum(x['type'] == 'blob' for x in tree['tree']))))
    else:
        identity = json.loads((out / 'identity.json').read_text())
        tree = json.loads((out / 'tree.json').read_text())
        assert tree['sha'] == identity['tree']
        blobs = {x['path']: x for x in tree['tree'] if x['type'] == 'blob'}
        for name in a.paths:
            assert name in blobs and blobs[name]['size'] <= 2**20
            relative = Path(name)
            assert not relative.is_absolute() and '..' not in relative.parts
            blob = blobs[name]
            cache = out / ('blob-' + blob['sha'] + '.json')
            data = get(API + '/git/blobs/' + blob['sha'], cache)
            assert data['encoding'] == 'base64' and data['sha'] == blob['sha']
            content = base64.b64decode(data['content'])
            assert len(content) == blob['size']
            assert hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == blob['sha']
            target = out / 'sources' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            assert not target.exists()
            target.write_bytes(content)
            record.setdefault('files', []).append(dict(path=name, bytes=len(content), blob=blob['sha'],
                sha256=hashlib.sha256(content).hexdigest(), commit=identity['commit']))
            save(out / 'requests.json', record)
        print(json.dumps(dict(files=record['files'], downloaded_code_executed=False)))


if __name__ == '__main__':
    main()
