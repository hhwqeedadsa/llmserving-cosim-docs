"""Bounded read-only public source acquisition; no retries or upstream execution."""
import argparse
import hashlib
import base64
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request


def load(p): return json.loads(p.read_bytes())
def save(p,v): p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
def sha(data): return hashlib.sha256(data).hexdigest()


class Fetch:
    def __init__(self,root):
        self.root=root;root.mkdir(exist_ok=True)
        self.path=root/'http.json';self.rows=load(self.path) if self.path.exists() else []

    def get(self,url,name):
        assert len(self.rows)<24 and url not in {r['url'] for r in self.rows},'closed request or HTTP count budget'
        remaining=8*2**20-sum(r['bytes'] for r in self.rows)
        cap=min(4*2**20,remaining);assert cap>0
        assert sum(r['elapsed_seconds'] for r in self.rows)<180
        target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True)
        assert not target.exists()
        start=time.monotonic();error=None;status=None
        deadline=min(10,180-sum(r['elapsed_seconds'] for r in self.rows))
        command=[sys.executable,'-I','-B',str(Path(__file__).resolve()),'--request',url,str(target),str(cap)]
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=deadline,check=True)
            response=json.loads(result.stdout);status=response['status'];error=response['error']
        except subprocess.TimeoutExpired:error='total request deadline exceeded; worker killed; no retry'
        elapsed=time.monotonic()-start
        if not target.exists():target.write_bytes(b'')
        body=target.read_bytes()
        row=dict(url=url,path=name,status=status,error=error,bytes=len(body),sha256=sha(body),elapsed_seconds=elapsed)
        self.rows.append(row);save(self.path,self.rows)
        assert len(body)<=cap and elapsed<=10.5,'request resource bound exceeded; preserve failure and stop'
        return body if status==200 and not error else None

    def metadata(self,repo,ref,label):
        raw=self.get('https://api.github.com/repos/'+repo+'/commits/'+ref,label+'/commit.json')
        if raw is None:return
        commit=json.loads(raw);revision=commit['sha'];tree=commit['commit']['tree']['sha']
        raw=self.get('https://api.github.com/repos/'+repo+'/git/trees/'+tree+'?recursive=1',label+'/tree.json')
        if raw is None:return
        tree_data=json.loads(raw);assert not tree_data.get('truncated')
        save(self.root/label/'identity.json',dict(repo=repo,requested_ref=ref,commit=revision,tree=tree))

    def files(self,label,paths):
        identity=load(self.root/label/'identity.json')
        tree={r['path']:r for r in load(self.root/label/'tree.json')['tree'] if r['type']=='blob'}
        assert len(paths)<=(6 if label=='clem' else 10)
        for path in paths:
            assert path in tree,path
            row=tree[path];assert row['size']<=4*2**20
            raw_path=label+'/sources/'+path;response_path=label+'/responses/'+path+'.json'
            assert not (self.root/raw_path).exists(),'source already attempted; do not retry'
            attempted=[r for r in self.rows if r['path'].startswith(label+'/sources/') or r['path'].startswith(label+'/responses/')]
            assert len(attempted)<(6 if label=='clem' else 10)
            response=self.get('https://api.github.com/repos/'+identity['repo']+'/git/blobs/'+row['sha'],response_path)
            if response is None:continue
            data=json.loads(response);assert data['encoding']=='base64'
            raw=base64.b64decode(data['content']);target=self.root/raw_path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            assert len(raw)==row['size']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['sha']


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--mode',choices=('metadata','clem','network'),required=True)
    p.add_argument('--paths',nargs='*');a=p.parse_args();f=Fetch(a.root.resolve())
    if a.mode=='metadata':
        f.metadata('aliyun/SimAI-CLEM','HEAD','clem')
        f.metadata('aliyun/ns-3-alibabacloud','dev%2Fclem','network')
    elif a.mode=='clem':
        tree={r['path'] for r in load(f.root/'clem/tree.json')['tree']}
        allowed=('README.md','nccl_patch','nccl_tests_patch','run_nccl_16A100.sh','LICENSE','NOTICE')
        paths=a.paths or [name for name in allowed if name in tree]
        assert all(name in allowed for name in paths)
        f.files('clem',paths)
    else:
        assert a.paths and len(a.paths)<=10;f.files('network',a.paths)


def request_once(url,target,cap):
    body=b'';status=None;error=None
    request=urllib.request.Request(url,headers={'User-Agent':'moe-cosim-source-audit','Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            status=response.status;body=response.read(cap+1)
    except urllib.error.HTTPError as e:
        status=e.code;body=e.read(cap+1);error=str(e)
    except Exception as e:error=type(e).__name__+': '+str(e)
    Path(target).write_bytes(body);print(json.dumps(dict(status=status,error=error)))


if __name__=='__main__':
    if sys.argv[1:2]==['--request']:request_once(sys.argv[2],sys.argv[3],int(sys.argv[4]))
    else:main()
