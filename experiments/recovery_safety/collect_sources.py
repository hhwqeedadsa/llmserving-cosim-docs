"""Read fixed local sources and a small set of primary recovery/security pages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[2]
REMOTE=[('nccl_errors','https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/communicators.html'),
 ('elastic','https://docs.pytorch.org/docs/stable/elastic/run.html'),
 ('redmark','https://www.usenix.org/conference/usenixsecurity21/presentation/rothenberger'),
 ('moe_fault_search','https://export.arxiv.org/api/query?search_query=all:mixture-of-experts+AND+all:fault&start=0&max_results=5'),
 ('rdma_recovery_search','https://export.arxiv.org/api/query?search_query=all:RDMA+AND+all:recovery&start=0&max_results=5')]

FOLLOWUP=[('varuna','https://arxiv.org/html/2603.28001v1'),
 ('byteexpress','https://arxiv.org/html/2610.12815v2'),
 ('optinic','https://arxiv.org/html/2512.22743v1'),
 ('moc','https://arxiv.org/html/2408.04307v3'),
 ('elastic_versioned','https://docs.pytorch.org/docs/2.14/elastic/run.html'),
 ('moe_recovery_search','https://export.arxiv.org/api/query?search_query=all:expert+AND+all:inference+AND+(all:failure+OR+all:recovery)&start=0&max_results=5')]

TARGETED=[('eep','https://arxiv.org/html/2605.10670v1'),
 ('rcu','https://www.kernel.org/doc/html/latest/RCU/whatisRCU.html'),
 ('nccl_fixed_comms','https://raw.githubusercontent.com/NVIDIA/nccl/12df1a11afad322be5a204a2db890161cbf8131d/docs/userguide/source/api/comms.rst')]

class Plain(HTMLParser):
    def __init__(self):super().__init__();self.lines=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.skip+=1
        if tag in ('p','div','li','h1','h2','h3','br'):self.lines.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.skip-=1
    def handle_data(self,data):
        if not self.skip:self.lines.append(data)

def save(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--phase',choices=['initial','followup','targeted'],default='initial');a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False,parents=True)
    local=[]
    groups=[('deepep_modern_20261022','docs/review_artifacts/deepep_modern_20261022/sources.json',{
       'deep_ep/include/deep_ep/comm/barrier.cuh':[(30,48)],
       'deep_ep/include/deep_ep/comm/handle.cuh':[(178,200),(204,216)],
       'deep_ep/include/deep_ep/impls/ep/dispatch.cuh':[(180,201)],
       'csrc/buffers/ep.hpp':[(649,663)]}),
       ('nccl_gin_20261022','docs/review_artifacts/nccl_gin_20261022/sources.json',{
        'docs/userguide/source/api/device_gin.rst':[(33,50),(70,79),(95,108)]})]
    for directory,manifest,wanted in groups:
        base=ROOT/'data/research'/directory;meta=json.loads((ROOT/manifest).read_text());tree=json.loads((base/'tree.json').read_text())
        for path,ranges in wanted.items():
            row=next(r for r in meta['sources'] if r['path']==path)
            data=(base/'sources'/path).read_bytes()
            assert hashlib.sha256(data).hexdigest()==row['sha256']
            assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['git_blob_sha']
            assert any(e['path']==path and e['sha']==row['git_blob_sha'] for e in tree['tree'])
            lines=data.decode().splitlines();local.append({k:row[k] for k in ('path','commit','git_blob_sha','sha256','public_url')})
            local[-1]['excerpts']=[dict(first=first,last=last,text='\n'.join(lines[first-1:last])) for first,last in ranges]
    save(out/'fixed-sources.json',dict(passed=True,sources=local))
    def fetch(item):
        name,url=item;r=dict(id=name,url=url,accessed_utc=datetime.now(timezone.utc).isoformat())
        try:
            with urlopen(Request(url,headers={'User-Agent':'MoE-recovery-evidence-review'}),timeout=15) as response:
                data=response.read(2*2**20+1);r.update(status_code=response.status,resolved_url=response.url)
            assert len(data)<=2*2**20,'source over 2MiB'
            (out/(name+'.html')).write_bytes(data);parser=Plain();parser.feed(data.decode('utf-8'))
            text='\n'.join(' '.join(s.split()) for s in ''.join(parser.lines).splitlines() if s.strip())
            (out/(name+'.txt')).write_text(text)
            r.update(status='read',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        except Exception as e:r.update(status='unavailable',error=str(e))
        return r
    with ThreadPoolExecutor(max_workers=4) as pool:remote=list(pool.map(fetch,dict(initial=REMOTE,followup=FOLLOWUP,targeted=TARGETED)[a.phase]))
    assert sum(r.get('bytes',0) for r in remote)<=12*2**20
    save(out/'remote-sources.json',dict(sources=remote,scope='selected primary sources; search metadata is not a novelty proof'))
    print(json.dumps(dict(fixed_sources=len(local),remote=remote)))
if __name__=='__main__':main()
