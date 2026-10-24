"""Bounded fixed-version source acquisition; never run downloaded library code."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
PROJECTS={
    'nccl':('NVIDIA/nccl','12df1a11afad322be5a204a2db890161cbf8131d','nccl_gin_20261022'),
    'deepep':('deepseek-ai/DeepEP','f6aa38fe06cd4a0489e7730365d4e0114970a2f3','deepep_modern_20261022'),
}
CACHED={
    'nccl':['src/dev_runtime.cc','src/nccl.h.in','src/include/nccl_device/core.h',
            'src/include/nccl_device/gin.h','src/gin/gin_host.cc'],
    'deepep':['csrc/kernels/comm/context.cpp','csrc/buffers/ep.hpp',
              'deep_ep/include/deep_ep/comm/barrier.cuh',
              'deep_ep/include/deep_ep/impls/ep/dispatch.cuh'],
}
NEW=['src/init.cc','docs/userguide/source/api/comms.rst','docs/userguide/source/usage/communicators.rst']


def save(p,v):p.write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def blob(raw):return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def add(output,project,path,fetch=False):
    repository,commit,cache=PROJECTS[project];cache=ROOT/'data/research'/cache
    tree=json.loads((output/'provenance'/project/'tree.json').read_text())
    entry=next(r for r in tree['tree'] if r['path']==path and r['type']=='blob')
    rows=json.loads((output/'sources.json').read_text());requests=json.loads((output/'requests.json').read_text())
    if any(r['project']==project and r['path']==path for r in rows+requests):raise ValueError('no duplicate acquisition/retry')
    if fetch:
        if len(requests)>=6:raise ValueError('six-request cap')
        response=output/'responses'/(project+'-'+str(len(requests))+'.json')
        command=['curl','-q','--fail','--silent','--show-error','--connect-timeout','3','--max-time','8',
                 '--max-filesize',str(2**20),'--output',str(response),
                 f'https://api.github.com/repos/{repository}/git/blobs/{entry["sha"]}']
        started=time.monotonic();result=subprocess.run(command,capture_output=True,timeout=10);elapsed=time.monotonic()-started
        row=dict(project=project,path=path,command=command,cwd=str(Path.cwd()),exit_code=result.returncode,
                 elapsed_seconds=elapsed,accessed_utc=datetime.now(timezone.utc).isoformat(),
                 stderr=result.stderr.decode(errors='replace'),response=str(response.relative_to(output)))
        if response.exists():row.update(response_bytes=response.stat().st_size,response_sha256=sha(response.read_bytes()))
        requests.append(row);save(output/'requests.json',requests)
        if result.returncode or elapsed>8.5:raise RuntimeError('bounded request failed; preserved without retry')
        value=json.loads(response.read_text());assert value['encoding']=='base64' and value['sha']==entry['sha']
        raw=base64.b64decode(value['content'],validate=False);method='fixed-git-blob'
    else:
        source=cache/'sources'/path
        if not source.exists():
            source=Path('/dev/shm/moe-window-lifecycle-20261024/inputs/sources')/project/path
        raw=source.read_bytes();method='fixed-cache'
    assert len(raw)==entry['size'] and blob(raw)==entry['sha']
    target=output/'sources'/project/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    rows.append(dict(project=project,repository=repository,commit=commit,path=path,method=method,
                     bytes=len(raw),sha256=sha(raw),git_blob_sha=entry['sha'],
                     url=f'https://github.com/{repository}/blob/{commit}/{path}'))
    save(output/'sources.json',rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume-preparation',action='store_true',help='Resume only the saved five-cache/no-request preparation failure')
    p.add_argument('--cached',nargs='+',help='Additional fixed-cache PROJECT:PATH entries')
    p.add_argument('--additional',help='One additional NCCL path in the pinned Git tree, no retry')
    a=p.parse_args()
    if a.cached or a.additional:
        for spec in a.cached or []:
            project,path=spec.split(':',1);add(a.output,project,path)
        if a.additional:add(a.output,'nccl',a.additional,True)
        return
    if a.resume_preparation:
        rows=json.loads((a.output/'sources.json').read_text())
        assert [(r['project'],r['path']) for r in rows]==[('nccl',p) for p in CACHED['nccl']]
        assert json.loads((a.output/'requests.json').read_text())==[]
    else:
        a.output.mkdir();(a.output/'responses').mkdir();save(a.output/'sources.json',[]);save(a.output/'requests.json',[])
    for project,(_,commit,cache) in PROJECTS.items():
        if a.resume_preparation and project=='nccl':continue
        cache=ROOT/'data/research'/cache;dest=a.output/'provenance'/project;dest.mkdir(parents=True,exist_ok=a.resume_preparation)
        tree=json.loads((cache/'tree.json').read_text());assert not tree.get('truncated',False)
        if (cache/'commit.json').exists():
            value=json.loads((cache/'commit.json').read_text());assert value['sha']==commit and value['tree']['sha']==tree['sha']
        else:
            value=json.loads((cache/'ref.json').read_text());origin=json.loads((cache/'tree-source.json').read_text())
            assert value['object']['sha']==origin['commit']==commit
            assert any(r['sha256']==sha((cache/'tree.json').read_bytes()) and '/git/trees/'+commit+'?' in r['url'] for r in origin['requests'])
        for name in ('commit.json','tree.json','ref.json','tree-source.json'):
            if (cache/name).exists():shutil.copyfile(cache/name,dest/name)
        for path in CACHED[project]:add(a.output,project,path)
    for path in NEW:add(a.output,'nccl',path,True)
    print(json.dumps(dict(source_files=len(json.loads((a.output/'sources.json').read_text())),requests=3)))


if __name__=='__main__':main()
