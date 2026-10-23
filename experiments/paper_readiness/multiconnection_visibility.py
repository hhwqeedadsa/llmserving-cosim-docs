"""Source audit for a fixed multi-connection visibility question; no GPU/network simulation."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

NCCL = '12df1a11afad322be5a204a2db890161cbf8131d'
DEEPEP = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
NEW = ('src/include/nccl_device/gin_barrier.h',
       'src/include/nccl_device/impl/gin_barrier__funcs.h',
       'src/include/nccl_device/impl/barrier__funcs.h')
OLD = ('docs/userguide/source/api/device_gin.rst',
       'src/include/nccl_device/gin.h',
       'src/include/nccl_device/impl/gin__funcs.h',
       'src/include/nccl_device/gin/gdaki/gin_gdaki.h',
       'src/transport/net_ib/gdaki/gin_host_gdaki.cc',
       'src/gin/gin_host.cc', 'src/devcomm/devcomm_v23000.cc')
EP = ('deep_ep/include/deep_ep/comm/barrier.cuh',
      'deep_ep/include/deep_ep/comm/handle.cuh')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def git_blob(data):
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


def collect(repo, output, finish_unrequested=False):
    assert output.parent.is_dir()
    assert shutil.disk_usage(output.parent).free > 8*2**30+32*2**20
    if finish_unrequested:
        assert output.is_dir() and (output/'requests.json').is_file()
    else:
        output.mkdir()
    nc = repo/'data/research/nccl_gin_20261022'
    ep = repo/'data/research/deepep_modern_20261022'
    for name in ('commit.json', 'identity.json'):
        shutil.copyfile(nc/name, output/('nccl-'+name))
    shutil.copyfile(ep/'tree-source.json', output/'deepep-tree-source.json')
    trees = {}
    for label, cache in (('nccl', nc), ('deepep', ep)):
        raw = (cache/'tree.json').read_bytes()
        tree = json.loads(raw)
        assert not tree['truncated']
        trees[label] = {x['path']: x for x in tree['tree']}
        (output/(label+'-tree.json')).write_bytes(raw)
    sources = []
    requests = json.loads((output/'requests.json').read_text()) if finish_unrequested else []

    def record(label, relative, data, origin):
        row = trees[label][relative]
        assert row['type']=='blob' and len(data)==row['size'] and git_blob(data)==row['sha']
        path = output/label/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sources.append(dict(project=label, commit=NCCL if label=='nccl' else DEEPEP,
            path=relative, blob=row['sha'], sha256=hashlib.sha256(data).hexdigest(),
            size=len(data), origin=origin))

    for relative in OLD:
        record('nccl', relative, (nc/'sources'/relative).read_bytes(), 'existing verified cache')
    for relative in EP:
        record('deepep', relative, (ep/'sources'/relative).read_bytes(), 'existing verified cache')
    save(output/'sources.json', sources)
    # Each fetch has its own hard process timeout and byte ceiling. No retries,
    # execution, imports, or installation of the fetched code are permitted.
    worker = """import sys,urllib.request
r=urllib.request.Request(sys.argv[1],headers={'User-Agent':'moe-cosim-source-audit'})
with urllib.request.urlopen(r,timeout=15) as f:
 b=f.read(262145)
 if len(b)>262144:raise ValueError('source response exceeds budget')
 sys.stdout.buffer.write(b)
"""
    for relative in NEW:
        if any(x.get('path')==relative or x['url'].endswith('/'+relative) for x in requests):
            existing = output/'nccl'/relative
            if existing.is_file():
                record('nccl', relative, existing.read_bytes(), 'previous successful response')
            continue
        url = ('https://api.github.com/repos/NVIDIA/nccl/git/blobs/'+trees['nccl'][relative]['sha']
               if finish_unrequested else f'https://raw.githubusercontent.com/NVIDIA/nccl/{NCCL}/{relative}')
        try:
            result = subprocess.run([sys.executable, '-c', worker, url],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=18)
            requests.append(dict(url=url, path=relative, exit_code=result.returncode,
                bytes=len(result.stdout), stderr=result.stderr.decode(errors='replace')))
            save(output/'requests.json', requests)
            assert sum(x['bytes'] for x in requests)<=768*1024
            if result.returncode:
                continue
            data = result.stdout
            if finish_unrequested:
                response = json.loads(data)
                assert response['sha']==trees['nccl'][relative]['sha'] and response['encoding']=='base64'
                data = base64.b64decode(response['content'], validate=False)
            record('nccl', relative, data, 'one fixed-revision HTTPS source response')
            save(output/'sources.json', sources)
        except subprocess.TimeoutExpired:
            requests.append(dict(url=url, path=relative, exit_code=None, timed_out=True, bytes=0))
            save(output/'requests.json', requests)
    save(output/'sources.json', sources)
    print(json.dumps(dict(source_files=len(sources), requests=len(requests),
        response_bytes=sum(x['bytes'] for x in requests))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--finish-unrequested', action='store_true',
                        help='Use existing receipts, never retry an attempted file; request only remaining blobs')
    args = parser.parse_args()
    collect(args.repo.resolve(), args.output.resolve(), args.finish_unrequested)


if __name__=='__main__':
    main()
