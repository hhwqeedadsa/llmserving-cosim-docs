"""Finite, no-retry upstream source intake; never changes a dependency."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

FILES=('rxe_req.c','rxe_resp.c','rxe_comp.c','rxe_opcode.c','rxe_hdr.h',
       'rxe_loc.h','rxe_verbs.h','rxe.h','rxe_opcode.h','rxe_net.c')
API='https://api.github.com/repos/torvalds/linux/'
PARTS=('drivers','infiniband','sw','rxe')


def load(p):return json.loads(Path(p).read_bytes())
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def git_hash(kind,raw):return hashlib.sha1(kind.encode()+b' '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def tree_hash(rows):
    rows=sorted(rows,key=lambda r:r['path'].encode()+(b'/' if r['type']=='tree' else b''))
    return git_hash('tree',b''.join(r['mode'].lstrip('0').encode()+b' '+r['path'].encode()+b'\0'+bytes.fromhex(r['sha']) for r in rows))


def verify_tree(root):
    identity=load(root/'identity.json');commit=load(root/'commit.json')
    assert commit['sha']==identity['commit']
    current=commit['commit']['tree']['sha']
    assert current==identity['root_tree']
    for entry in identity['trees']:
        value=load(root/entry['path']);assert value['sha']==tree_hash(value['tree'])==current==entry['sha']
        if entry['next']:
            item,=[v for v in value['tree'] if v['path']==entry['next'] and v['type']=='tree'];current=item['sha']
    table={v['path']:v for v in value['tree']}
    for entry in identity['sources']:
        raw=(root/entry['local']).read_bytes();filename=Path(entry['path']).name
        assert git_hash('blob',raw)==entry['git_blob']==table[filename]['sha']
        assert len(raw)==entry['bytes']==table[filename]['size'] and sha(root/entry['local'])==entry['sha256']
    return identity


def initialize(repo,stage):
    stage.mkdir();observed=stage/'observed';observed.mkdir()
    original=repo/'runs/rc_operation_reference_20261027/upstream'
    identity=verify_tree(original)
    assert identity['commit']=='adc218676eef25575469234709c2d87185ca223a' and len(identity['sources'])==9
    old=observed/'v6.12';old.mkdir();(old/'sources').mkdir()
    names=['commit.json','identity.json']+[r['path'] for r in identity['trees']]+[r['local'] for r in identity['sources']]
    for name in names:shutil.copyfile(original/name,old/name)
    verify_tree(old)
    new=observed/'upstream';new.mkdir();(new/'sources').mkdir();(new/'responses').mkdir()
    save(stage/'acquisition.json',dict(status='in_progress',step='commit',requests=[],trees=[],sources=[],
         old_source_identity_sha256=sha(old/'identity.json')))


def worker(url,path,cap):
    body=b'';metadata={}
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'moe-rc-completion-audit','Accept':'application/vnd.github+json'})
        with urllib.request.urlopen(req,timeout=8) as response:
            metadata['http_status']=response.status;body=response.read(cap+1)
    except urllib.error.HTTPError as error:
        metadata.update(http_status=error.code,error=type(error).__name__);body=error.read(cap+1)
    except Exception as error:metadata['error']=type(error).__name__+': '+str(error)
    Path(path).write_bytes(body);print(json.dumps(metadata))


def get(stage,state,url,name):
    rows=state['requests'];used=sum(r.get('bytes',0) for r in rows)
    assert len(rows)<20 and url not in {r['url'] for r in rows}
    cap=min(512*1024,3*1024*1024-used);assert cap>0
    path=stage/'observed/upstream'/name;assert not path.exists()
    row=dict(url=url,path=name,status='attempting');rows.append(row);save(stage/'acquisition.json',state)
    start=time.monotonic()
    try:
        child=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'worker',url,str(path),str(cap)],
                             capture_output=True,timeout=10)
        metadata=json.loads(child.stdout) if child.returncode==0 else dict(error='worker_failed',exit_code=child.returncode)
    except subprocess.TimeoutExpired:metadata=dict(error='total_request_deadline_exceeded')
    body=path.read_bytes() if path.exists() else b''
    row.update(metadata,elapsed_seconds=time.monotonic()-start,bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    passed=metadata.get('http_status')==200 and not metadata.get('error') and len(body)<=cap and row['elapsed_seconds']<=10.5
    row['status']='completed' if passed else 'failed'
    if not passed:state['status']='stopped_after_HTTP_failure'
    save(stage/'acquisition.json',state);assert passed,row
    return json.loads(body)


def acquire(repo,stage,count):
    if not stage.exists():initialize(repo,stage)
    state=load(stage/'acquisition.json');assert state['status']=='in_progress',state['status']
    root=stage/'observed/upstream';old=stage/'observed/v6.12'
    remaining=count
    while remaining and state['status']=='in_progress':
        if state['step']=='commit':
            value=get(stage,state,API+'commits/master','commit.json');remaining-=1
            state.update(commit=value['sha'],root_tree=value['commit']['tree']['sha'],current_tree=value['commit']['tree']['sha'],step='trees')
        elif state['step']=='trees':
            level=len(state['trees']);name='tree-'+str(level)+'.json'
            value=get(stage,state,API+'git/trees/'+state['current_tree'],name);remaining-=1
            assert not value.get('truncated') and value['sha']==state['current_tree']==tree_hash(value['tree'])
            component=PARTS[level] if level<4 else None
            state['trees'].append(dict(path=name,sha=value['sha'],next=component))
            if component:
                entry,=[r for r in value['tree'] if r['path']==component and r['type']=='tree'];state['current_tree']=entry['sha']
            else:state['step']='sources'
        else:
            table={r['path']:r for r in load(root/'tree-4.json')['tree']}
            done={Path(r['path']).name for r in state['sources']}
            candidates=[n for n in FILES if n not in done]
            if not candidates:
                state.update(status='completed',step='done');break
            name=candidates[0]
            if name not in table:
                state['sources'].append(dict(path='drivers/infiniband/sw/rxe/'+name,missing=True));save(stage/'acquisition.json',state);continue
            entry=table[name];assert entry['type']=='blob' and entry['size']<=256*1024
            cached=old/'sources'/name
            if cached.exists() and git_hash('blob',cached.read_bytes())==entry['sha']:
                raw=cached.read_bytes();origin='old pinned source exactly matches new pinned blob'
            else:
                value=get(stage,state,API+'git/blobs/'+entry['sha'],'responses/'+name+'.json');remaining-=1
                assert value['encoding']=='base64' and value['sha']==entry['sha']
                raw=base64.b64decode(value['content']);origin='new pinned Git blob'
            assert len(raw)==entry['size'] and git_hash('blob',raw)==entry['sha']
            (root/'sources'/name).write_bytes(raw)
            state['sources'].append(dict(path='drivers/infiniband/sw/rxe/'+name,local='sources/'+name,
                git_blob=entry['sha'],sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),origin=origin))
        save(stage/'acquisition.json',state)
    if state['step']=='sources' and len(state['sources'])==len(FILES):state.update(status='completed',step='done')
    save(stage/'acquisition.json',state)
    if state['status']=='completed':
        assert not any(s.get('missing') for s in state['sources']),'source disappeared; do not infer absence of semantics'
        save(root/'identity.json',{k:state[k] for k in ('commit','root_tree','trees','sources')});verify_tree(root)
    print(json.dumps(dict(status=state['status'],step=state['step'],HTTP=len(state['requests']),sources=len(state['sources'])),ensure_ascii=False))


if __name__=='__main__':
    if sys.argv[1:2]==['worker']:worker(sys.argv[2],sys.argv[3],int(sys.argv[4]))
    else:
        p=argparse.ArgumentParser(description=__doc__)
        for name in ('repository','stage'):p.add_argument('--'+name,type=Path,required=True)
        p.add_argument('--requests',type=int,default=3);a=p.parse_args();assert 1<=a.requests<=3
        acquire(a.repository.resolve(),a.stage.resolve(),a.requests)
