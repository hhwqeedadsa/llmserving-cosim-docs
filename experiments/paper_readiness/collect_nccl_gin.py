"""Collect bounded, fixed NCCL GIN sources for static research, never install them."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import resource
import sys
import time
from urllib.request import Request, urlopen

API = 'https://api.github.com/repos/NVIDIA/nccl/git/'
LIMIT = 2*2**20


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=('identity','files'), required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tag', default='v2.32.3-1')
    p.add_argument('--batch', default='first')
    p.add_argument('--paths', nargs='*')
    a = p.parse_args()
    resource.setrlimit(resource.RLIMIT_AS,(2*2**30,2*2**30))
    resource.setrlimit(resource.RLIMIT_CPU,(60,60))
    begin=time.monotonic()
    out=a.output.resolve()

    def fetch(url):
        remaining=59-(time.monotonic()-begin)
        assert remaining>0,'Collection wall deadline expired'
        with urlopen(Request(url,headers={'User-Agent':'MoE-GIN-source-audit',
            'Accept':'application/vnd.github+json'}),timeout=min(20,remaining)) as response:
            raw=response.read(LIMIT+1)
            assert response.status==200 and len(raw)<=LIMIT
        return raw

    if a.phase=='identity':
        assert re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+-[0-9]+',a.tag) and not a.paths
        out.mkdir(parents=True,exist_ok=False)
        record=dict(tag=a.tag,accessed_utc=datetime.now(timezone.utc).isoformat(),
                    argv=sys.argv,cwd=str(Path.cwd()),requests=[])
        def query(url,name):
            raw=fetch(url)
            (out/name).write_bytes(raw)
            record['requests'].append(dict(url=url,file=name,bytes=len(raw),sha256=sha(raw)))
            return json.loads(raw)
        try:
            ref=query(API+'ref/tags/'+a.tag,'ref.json')['object']
            if ref['type']=='tag':
                ref=query(API+'tags/'+ref['sha'],'tag.json')['object']
            assert ref['type']=='commit' and re.fullmatch('[0-9a-f]{40}',ref['sha'])
            commit=query(API+'commits/'+ref['sha'],'commit.json')
            assert commit['sha']==ref['sha']
            record.update(commit=commit['sha'],tree=commit['tree']['sha'])
            tree=query(API+'trees/'+record['tree']+'?recursive=1','tree.json')
            assert not tree['truncated'] and tree['sha']==record['tree']
            record['status']='read'
            for item in tree['tree']:
                if item['type']=='blob' and any(k in item['path'].lower() for k in ('/gin','/gdaki')):
                    print(item['path'],item.get('size'))
        except Exception as error:
            record.update(status='unavailable',reason=str(error))
        record['elapsed_seconds']=time.monotonic()-begin
        save(out/'identity.json',record)
        assert record['status']=='read','Preserve failure; no automatic retry or version substitution'
        return
    assert a.paths and 1<=len(a.paths)<=6 and len(set(a.paths))==len(a.paths)
    assert re.fullmatch('[a-z0-9_]{1,32}',a.batch)
    dest=out/('files-'+a.batch+'.json')
    assert not dest.exists()
    identity=json.loads((out/'identity.json').read_text())
    assert identity['status']=='read'
    tree_record=next(r for r in identity['requests'] if r['file']=='tree.json')
    assert sha((out/'tree.json').read_bytes())==tree_record['sha256']
    entries={i['path']:i for i in json.loads((out/'tree.json').read_text())['tree'] if i['type']=='blob'}
    previous=[json.loads(p.read_text()) for p in sorted(out.glob('files-*.json'))]
    seen={r['path'] for batch in previous for r in batch['sources']}
    assert len(seen)+len(a.paths)<=18
    prior_bytes=sum(r['bytes'] for r in identity['requests'])+sum(b['batch_response_bytes'] for b in previous)
    prior_seconds=identity['elapsed_seconds']+sum(b['batch_elapsed_seconds'] for b in previous)
    assert prior_bytes+len(a.paths)*LIMIT<=16*2**20
    assert prior_seconds+40<=120
    for name in a.paths:
        path=PurePosixPath(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert name in entries and name not in seen and entries[name]['size']<=LIMIT
        assert not (out/'sources'/name).exists()

    def collect(name):
        item=entries[name]
        url=API+'blobs/'+item['sha']
        record=dict(path=name,commit=identity['commit'],git_blob_sha=item['sha'],url=url,
                    public_url='https://github.com/NVIDIA/nccl/blob/'+identity['commit']+'/'+name)
        try:
            response=fetch(url)
            record['response_bytes']=len(response)
            blob=json.loads(response)
            assert blob['sha']==item['sha'] and blob['encoding']=='base64'
            raw=base64.b64decode(blob['content'])
            assert len(raw)==blob['size']==item['size']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==item['sha']
            target=out/'sources'/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw)
            record.update(status='read',bytes=len(raw),sha256=sha(raw))
        except Exception as error:
            record.update(status='unavailable',reason=str(error))
        return record

    with ThreadPoolExecutor(max_workers=3) as pool:
        records=list(pool.map(collect,a.paths))
    response_bytes=sum(r.get('response_bytes',0) for r in records)
    elapsed=time.monotonic()-begin
    result=dict(commit=identity['commit'],tag=identity['tag'],sources=records,
                batch_response_bytes=response_bytes,batch_elapsed_seconds=elapsed,
                cumulative_response_bytes=prior_bytes+response_bytes,cumulative_elapsed_seconds=prior_seconds+elapsed,
                argv=sys.argv,cwd=str(Path.cwd()),kernel_execution=False,dependency_change=False)
    save(dest,result)
    assert prior_bytes+response_bytes<=16*2**20 and prior_seconds+elapsed<=120
    assert all(r['status']=='read' for r in records),'Preserve failure; do not retry failed files in another batch'
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
