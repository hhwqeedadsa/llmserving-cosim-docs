"""Collect bounded pinned source blobs; never execute downloaded source code."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = {
    'nccl': ('NVIDIA/nccl', '12df1a11afad322be5a204a2db890161cbf8131d', 'nccl_gin_20261022'),
    'deepep': ('deepseek-ai/DeepEP', 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3', 'deepep_modern_20261022'),
}
CACHED = {
    'nccl': ['src/gin/gin_host.cc', 'src/transport/net_ib/gdaki/gin_host_gdaki.cc'],
    'deepep': ['csrc/buffers/ep.hpp', 'csrc/kernels/comm/context.cpp',
               'deep_ep/include/deep_ep/comm/barrier.cuh', 'deep_ep/include/deep_ep/impls/comm/barrier.cuh'],
}
NEW = {
    'nccl': ['src/dev_runtime.cc', 'src/nccl.h.in', 'docs/userguide/source/api/device_setup.rst'],
    'deepep': ['csrc/kernels/comm/barrier.hpp', 'csrc/comm/barrier.hpp'],
}


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def collect(output):
    output.mkdir()
    rows = [];requests = [];downloaded = 0
    for project, (repository, commit, cache) in PROJECTS.items():
        original = ROOT / 'data/research' / cache
        tree = json.loads((original / 'tree.json').read_text())
        provenance = output / 'provenance' / project
        provenance.mkdir(parents=True)
        for name in ('tree.json', 'commit.json', 'ref.json', 'identity.json', 'tree-source.json'):
            if (original / name).exists():
                shutil.copyfile(original / name, provenance / name)
        entries = {r['path']: r for r in tree['tree'] if r['type'] == 'blob'}
        for path in CACHED[project] + NEW[project]:
            entry = entries[path]
            destination = output / 'sources' / project / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path in CACHED[project]:
                raw = (original / 'sources' / path).read_bytes()
                method = 'fixed-cache'
            else:
                response = output / 'responses' / project / (path.replace('/', '_') + '.json')
                response.parent.mkdir(parents=True, exist_ok=True)
                url = f'https://api.github.com/repos/{repository}/git/blobs/{entry["sha"]}'
                command = ['curl', '-q', '--fail', '--silent', '--show-error',
                           '--connect-timeout', '3', '--max-time', '8', '--max-filesize', str(2**20),
                           '--output', str(response), url]
                started = time.monotonic()
                result = subprocess.run(command, capture_output=True, timeout=10)
                elapsed = time.monotonic() - started
                request = dict(url=url, command=command, cwd=str(Path.cwd()), exit_code=result.returncode,
                               elapsed_seconds=elapsed, response_file=str(response.relative_to(output)),
                               stderr=result.stderr.decode(errors='replace'),
                               accessed_utc=datetime.now(timezone.utc).isoformat())
                if response.exists():
                    request.update(response_bytes=response.stat().st_size, response_sha256=sha(response.read_bytes()))
                requests.append(request);save(output / 'requests.json', requests)
                if result.returncode or elapsed > 8.5:
                    raise RuntimeError('bounded fetch failed; preserved without retry: ' + path)
                value = json.loads(response.read_text())
                if value['encoding'] != 'base64' or value['sha'] != entry['sha']:
                    raise ValueError('unexpected blob response')
                raw = base64.b64decode(value['content']);method = 'fixed-git-blob-api'
                downloaded += len(raw)
                if downloaded > 2**20:
                    raise ValueError('source download total limit')
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if len(raw) != entry['size'] or blob != entry['sha']:
                raise ValueError('tree/blob mismatch: ' + path)
            destination.write_bytes(raw)
            rows.append(dict(project=project, repository=repository, commit=commit, path=path,
                             bytes=len(raw), sha256=sha(raw), git_blob_sha=blob, method=method,
                             url=f'https://github.com/{repository}/blob/{commit}/{path}'))
            save(output / 'sources.json', rows)
    print(json.dumps(dict(source_files=len(rows), fetched_files=len(requests), new_source_bytes=downloaded)))


def supplement(output):
    """One additional public usage document, within the six-request phase cap."""
    project = 'nccl';path = 'docs/userguide/source/usage/deviceapi.rst'
    repository, commit, _ = PROJECTS[project]
    rows = json.loads((output / 'sources.json').read_text())
    requests = json.loads((output / 'requests.json').read_text())
    if len(requests) != 5 or any(r['path'] == path for r in rows):
        raise ValueError('supplement requires original five requests, no retry')
    tree = json.loads((output / 'provenance/nccl/tree.json').read_text())
    entry = next(r for r in tree['tree'] if r['path'] == path)
    response = output / 'responses/nccl/usage_deviceapi.rst.json'
    url = f'https://api.github.com/repos/{repository}/git/blobs/{entry["sha"]}'
    command = ['curl', '-q', '--fail', '--silent', '--show-error', '--connect-timeout', '3',
               '--max-time', '8', '--max-filesize', str(2**20), '--output', str(response), url]
    started = time.monotonic();result = subprocess.run(command, capture_output=True, timeout=10)
    elapsed = time.monotonic() - started
    request = dict(url=url, command=command, cwd=str(Path.cwd()), exit_code=result.returncode,
                   elapsed_seconds=elapsed, response_file=str(response.relative_to(output)),
                   stderr=result.stderr.decode(errors='replace'), accessed_utc=datetime.now(timezone.utc).isoformat())
    if response.exists():
        request.update(response_bytes=response.stat().st_size, response_sha256=sha(response.read_bytes()))
    requests.append(request);save(output / 'requests.json', requests)
    if result.returncode or elapsed > 8.5:
        raise RuntimeError('supplement failed without retry')
    value = json.loads(response.read_text());raw = base64.b64decode(value['content'])
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if value['encoding'] != 'base64' or value['sha'] != entry['sha'] or blob != entry['sha'] or len(raw) != entry['size']:
        raise ValueError('supplement blob mismatch')
    destination = output / 'sources/nccl' / path;destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    rows.append(dict(project=project, repository=repository, commit=commit, path=path, bytes=len(raw),
                     sha256=sha(raw), git_blob_sha=blob, method='fixed-git-blob-api',
                     url=f'https://github.com/{repository}/blob/{commit}/{path}'))
    save(output / 'sources.json', rows)
    print(json.dumps(dict(source_files=len(rows), fetched_files=len(requests), new_source_bytes=sum(r['bytes'] for r in rows if r['method']=='fixed-git-blob-api'))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--supplement', action='store_true')
    args = parser.parse_args()
    (supplement if args.supplement else collect)(args.output)
