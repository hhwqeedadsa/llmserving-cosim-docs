"""Bounded Linux RC source acquisition and offline Git-tree verification."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = 'https://api.github.com/repos/torvalds/linux/'
PREFIX = 'drivers/infiniband/sw/rxe/'
FILES = ('rxe_req.c', 'rxe_resp.c', 'rxe_hdr.h', 'rxe_comp.c', 'rxe_opcode.c', 'rxe_param.h', 'rxe_loc.h', 'rxe_verbs.h', 'rxe.h')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')


def digest(kind, raw):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def tree_hash(rows):
    rows = sorted(rows, key=lambda r: r['path'].encode() + (b'/' if r['type'] == 'tree' else b''))
    return digest('tree', b''.join(r['mode'].lstrip('0').encode() + b' ' + r['path'].encode()
                                + b'\0' + bytes.fromhex(r['sha']) for r in rows))


def get(root, url, name):
    ledger = root / 'http.json'
    rows = json.loads(ledger.read_bytes()) if ledger.exists() else []
    assert len(rows) < 20 and url not in {r['url'] for r in rows}, 'attempt closed or HTTP count limit'
    cap = min(256 * 1024, 2 * 1024 * 1024 - sum(r['bytes'] for r in rows))
    assert cap > 0
    target = root / name
    assert not target.exists()
    row = dict(url=url, path=name, bytes=0, status='attempting')
    rows.append(row)
    save(ledger, rows)  # A crash cannot silently authorize the same request again.
    started = time.monotonic()
    try:
        p = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                            'request', url, str(target), str(cap)], capture_output=True,
                           timeout=10, check=False)
        metadata = json.loads(p.stdout) if p.returncode == 0 else dict(error='worker failed', code=p.returncode)
    except subprocess.TimeoutExpired:
        metadata = dict(error='total request deadline exceeded; worker killed; no retry')
    elapsed = time.monotonic() - started
    body = target.read_bytes() if target.exists() else b''
    row.update(metadata, bytes=len(body), elapsed_seconds=elapsed, sha256=hashlib.sha256(body).hexdigest())
    save(ledger, rows)
    assert elapsed <= 10.5 and len(body) <= cap, 'resource violation preserved; stop'
    assert metadata.get('status') == 200 and not metadata.get('error'), row
    return json.loads(body)


def acquire(root, cached):
    root.mkdir(parents=True, exist_ok=False)
    commit = get(root, API + 'commits/v6.12', 'commit.json')
    revision, current = commit['sha'], commit['commit']['tree']['sha']
    chain = []
    for component in ('drivers', 'infiniband', 'sw', 'rxe', None):
        name = 'tree-' + str(len(chain)) + '.json'
        data = get(root, API + 'git/trees/' + current, name)
        assert not data.get('truncated') and data['sha'] == tree_hash(data['tree']) == current
        chain.append(dict(path=name, sha=current, next=component))
        if component:
            row, = [r for r in data['tree'] if r['path'] == component and r['type'] == 'tree']
            current = row['sha']
    entries = {r['path']: r for r in data['tree']}
    sources = []
    (root / 'sources').mkdir()
    for name in FILES:
        entry = entries[name]
        assert entry['type'] == 'blob' and entry['size'] <= 256 * 1024
        old = cached / name
        if old.exists():
            raw = old.read_bytes()
            assert digest('blob', raw) == entry['sha'], 'cache differs from pinned source'
            origin = 'existing source; newly verified against fixed tree'
        else:
            response = get(root, API + 'git/blobs/' + entry['sha'], name + '.response.json')
            assert response['encoding'] == 'base64' and response['sha'] == entry['sha']
            raw = base64.b64decode(response['content'], validate=False)
            origin = 'fixed Git blob response'
        assert len(raw) == entry['size'] and digest('blob', raw) == entry['sha']
        (root / 'sources' / name).write_bytes(raw)
        sources.append(dict(path=PREFIX + name, local='sources/' + name, git_blob=entry['sha'],
                            bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), origin=origin))
    save(root / 'identity.json', dict(repository='torvalds/linux', requested_ref='v6.12',
                                    commit=revision, root_tree=commit['commit']['tree']['sha'],
                                    trees=chain, sources=sources))


def verify(root):
    identity = json.loads((root / 'identity.json').read_bytes())
    commit = json.loads((root / 'commit.json').read_bytes())
    assert commit['sha'] == identity['commit'] and commit['commit']['tree']['sha'] == identity['root_tree']
    current = identity['root_tree']
    for row in identity['trees']:
        data = json.loads((root / row['path']).read_bytes())
        assert tree_hash(data['tree']) == data['sha'] == row['sha'] == current
        if row['next']:
            entry, = [r for r in data['tree'] if r['path'] == row['next'] and r['type'] == 'tree']
            current = entry['sha']
    entries = {PREFIX + r['path']: r for r in data['tree']}
    for row in identity['sources']:
        raw = (root / row['local']).read_bytes()
        assert row['git_blob'] == entries[row['path']]['sha'] == digest('blob', raw)
        assert row['bytes'] == len(raw) == entries[row['path']]['size']
        assert row['sha256'] == hashlib.sha256(raw).hexdigest()
    for row in json.loads((root / 'http.json').read_bytes()):
        raw = (root / row['path']).read_bytes()
        assert len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256']
        assert row['status'] == 200 and not row.get('error') and row['elapsed_seconds'] <= 10.5
    return dict(passed=True, commit=identity['commit'], trees=len(identity['trees']), sources=len(identity['sources']))


def extend_constants(root, name):
    """Fetch one previously unattempted core header from the pinned tree."""
    verify(root)
    identity = json.loads((root / 'identity.json').read_bytes())
    assert name in ('rxe_loc.h', 'rxe_verbs.h', 'rxe.h')
    assert 'sources/'+name not in {r['local'] for r in identity['sources']}
    data = json.loads((root / identity['trees'][-1]['path']).read_bytes())
    entry, = [r for r in data['tree'] if r['path'] == name]
    response = get(root, API + 'git/blobs/' + entry['sha'], name+'.response.json')
    assert response['encoding'] == 'base64'
    raw = base64.b64decode(response['content'])
    assert len(raw) == entry['size'] and digest('blob', raw) == entry['sha']
    (root / 'sources' / name).write_bytes(raw)
    (root / ('identity-before-'+name+'.json')).write_bytes((root / 'identity.json').read_bytes())
    identity['sources'].append(dict(path=PREFIX+name,local='sources/'+name,
        git_blob=entry['sha'],bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),origin='fixed Git blob response'))
    save(root / 'identity.json', identity)


def request(url, target, cap):
    body = b''
    metadata = {}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'moe-rc-reference', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            metadata['status'] = response.status
            body = response.read(cap + 1)
    except urllib.error.HTTPError as error:
        metadata.update(status=error.code, error=str(error))
        body = error.read(cap + 1)
    except Exception as error:
        metadata['error'] = type(error).__name__ + ': ' + str(error)
    Path(target).write_bytes(body)
    print(json.dumps(metadata))


if __name__ == '__main__':
    if sys.argv[1:2] == ['request']:
        request(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('action', choices=('acquire', 'verify', 'extend-constants'))
        parser.add_argument('--root', type=Path, required=True)
        parser.add_argument('--cached', type=Path)
        parser.add_argument('--name', choices=('rxe_loc.h','rxe_verbs.h','rxe.h'), default='rxe_loc.h')
        args = parser.parse_args()
        if args.action == 'acquire':
            acquire(args.root.resolve(), args.cached.resolve())
        elif args.action == 'extend-constants':
            extend_constants(args.root.resolve(), args.name)
        else:
            print(json.dumps(verify(args.root), sort_keys=True))
