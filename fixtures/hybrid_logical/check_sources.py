"""Admit four already cached fixed sources; no network, build or GPU execution."""
import argparse
import hashlib
import json
from pathlib import Path

REVISION = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
EXPECTED = {
    'deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh': 'd7e6e98aeb605392c668bf46de3520072f02b61f145cfc5779dd9c32f0e73db9',
    'deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh': '7a5091f7acd501fe0500564d1ef775e10150d880512e178e6c6bb40285eb1248',
    'deep_ep/include/deep_ep/layout/ep/token.cuh': '3510d2832b3aeaa99746cb2c3423bf68c6719e0ca56cd4eb408e9b533dbd58e6',
    'csrc/kernels/comm/context.cpp': 'c420b55314b9525dcb7e9f6c9d23a11cfe62a20268887493b76fc52881bb5e6a',
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists()
    load = lambda path: json.loads(path.read_text())
    tree = {r['path']: r['sha'] for r in load(a.cache/'tree.json')['tree'] if r['type'] == 'blob'}
    rows = {}
    for name in ('file-sources.json', 'file-sources-completion.json'):
        record = load(a.cache/name)
        assert record['commit'] == REVISION
        rows.update((r['path'], r) for r in record['sources'])
    sources = []
    for name, sha in EXPECTED.items():
        raw = (a.cache/'sources'/name).read_bytes()
        row = rows[name]
        assert row['commit'] == REVISION and hashlib.sha256(raw).hexdigest() == sha == row['sha256']
        blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        assert blob == row['git_blob_sha'] == tree[name]
        sources.append(dict(path=name, sha256=sha, git_blob_sha=blob, bytes=len(raw), url=row['public_url']))
    result = dict(passed=True, revision=REVISION, source_count=len(sources), sources=sources,
                  network_access=False, upstream_code_executed=False)
    a.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__': main()
