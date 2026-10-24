"""Freeze pinned provider sources for an offline retirement-contract audit."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def identity(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                git_blob_sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','acquired','output'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();repo=a.repo.resolve();acquired=a.acquired.resolve()
    rows=json.loads((acquired/'api-acquisition.json').read_text())+[json.loads((acquired/'umr-acquisition.json').read_text())]
    assert len(rows)==8 and all(r['status']=='read' for r in rows)
    commit=(acquired/'linux-commit.txt').read_text().strip()
    selected=[]
    for r in rows:
        name=('mlx5_' if '/hw/mlx5/' in r['path'] else '')+Path(r['path']).name
        p=acquired/'sources'/r['path'].replace('/','__');raw=p.read_bytes();d=identity(raw)
        assert d['sha256']==r['sha256'] and d['git_blob_sha']==r['git_blob_sha1']
        selected.append((p,dict(alias=name,project='linux',commit=commit,path=r['path'],url=r['url'],**d)))
    old=repo/'data/research/rc_atomic_20261022'
    for r in json.loads((old/'sources.json').read_text())['sources']:
        if Path(r['path']).name not in ('rxe_req.c','rxe_resp.c'):continue
        p=old/Path(r['path']).name;d=identity(p.read_bytes());assert d['sha256']==r['sha256']
        selected.append((p,dict(alias=p.name,project='linux',commit=commit,ref='v6.12',
            commit_resolution='original fixed-tag read; tag resolved this phase; no new content fetch',path=r['path'],url=r['url'],**d)))
    for manifest,folder,target,alias in (
        ('docs/review_artifacts/nccl_gin_20261022/sources.json','data/research/nccl_gin_20261022/sources',
         'src/transport/net_ib/gdaki/gin_host_gdaki.cc','gin_host_gdaki.cc'),
        ('docs/review_artifacts/deepep_modern_20261022/sources.json','data/research/deepep_modern_20261022/sources',
         'csrc/kernels/comm/context.cpp','deepep_context.cpp')):
        r=next(r for r in json.loads((repo/manifest).read_text())['sources'] if r['path']==target)
        p=repo/folder/target;d=identity(p.read_bytes());assert d['sha256']==r['sha256'] and d['git_blob_sha']==r['git_blob_sha']
        selected.append((p,dict(alias=alias,project='nccl' if alias.startswith('gin') else 'deepep',
            commit=r['commit'],path=target,url=r['public_url'],**d)))
    assert len(selected)==12 and len({r['alias'] for _,r in selected})==12
    a.output.mkdir();(a.output/'sources').mkdir()
    for p,r in selected:shutil.copy2(p,a.output/'sources'/r['alias'])
    for name in ('acquisition.json','api-acquisition.json','umr-acquisition.json','linux-commit.txt'):
        shutil.copy2(acquired/name,a.output/name)
    (a.output/'sources.json').write_text(json.dumps([r for _,r in selected],sort_keys=True,indent=2)+'\n')
    print(json.dumps(dict(source_files=12,immutable_linux_commit=commit,all_content_hashes_verified=True)))


if __name__=='__main__':main()
