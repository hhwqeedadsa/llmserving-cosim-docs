"""Fetch at most four prespecified-by-metadata held-out request traces, without retries."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

REV='27febb7b2d24169560a9f6f83d38eb53f94916cf'
PREFIX='cognitivecomputations/DeepSeek-R1-AWQ/mmlu'
REPO='core12345/MoE_expert_selection_trace'
OLD={'1472.json','1808.json','2675.json','6447.json'}


def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+16*2**20
    out.mkdir();start=time.monotonic();requests=[];total=0
    report=dict(dataset=REPO,revision=REV,prefix=PREFIX,requests=requests,completed=False,
        selection_rule='First four JSON paths sorted by numeric basename then path in first recursive tree response, excluding old basenames, each <=4MiB; no pagination or content-dependent replacements',
        raw_limit_bytes=4*2**20,total_response_limit_bytes=16*2**20,total_wall_limit_seconds=57)
    def fetch(url,target,cap):
        nonlocal total
        row=dict(url=url,file=target.name);requests.append(row);t=time.monotonic()
        try:
            remaining=57-(time.monotonic()-start);assert remaining>0
            # urllib timeout applies to socket operations and can restart across
            # addresses. A child deadline bounds the complete request as well.
            child=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--fetch-worker',url,str(target),
                str(min(cap,16*2**20-total))],timeout=min(20,remaining),capture_output=True,text=True)
            row['worker_exit_code']=child.returncode
            row['worker_result']=json.loads(child.stdout) if child.stdout else None
            assert child.returncode==0, 'Bounded request worker failed'
            data=target.read_bytes();row['status']=row['worker_result']['status']
            total+=len(data);row['bytes']=len(data)
            assert len(data)<=cap and total<=16*2**20
            target.write_bytes(data);row['sha256']=hashlib.sha256(data).hexdigest();return data
        except Exception as error:
            row['error_type']=type(error).__name__;row['http_error_code']=getattr(error,'code',None);raise
        finally:
            row['wall_seconds']=time.monotonic()-t;report['total_response_bytes']=total;save(out/'collection.json',report)
    try:
        # mmlu contains subject subdirectories, not directly the request files.
        # This correction was made after the original timed-out acquisition;
        # it does not repair that historical run or imply remote verification.
        tree_url='https://huggingface.co/api/datasets/'+REPO+'/tree/'+REV+'/'+quote(PREFIX,safe='/')+'?recursive=true&limit=1000'
        listing=json.loads(fetch(tree_url,out/'tree.json',2*2**20));assert isinstance(listing,list)
        candidates=[r for r in listing if r['type']=='file' and Path(r['path']).suffix=='.json'
            and Path(r['path']).stem.isdecimal() and Path(r['path']).name not in OLD and 0<r['size']<=4*2**20]
        selected=sorted(candidates,key=lambda r:(int(Path(r['path']).stem),r['path']))[:4];assert len(selected)==4
        assert len({Path(r['path']).name for r in selected})==4, 'Ambiguous duplicate output basenames; stop'
        report['selection']=selected;save(out/'collection.json',report)
        for row in selected:
            assert row['path'].startswith(PREFIX+'/') and Path(row['path']).name not in OLD
            data=fetch('https://huggingface.co/datasets/'+REPO+'/resolve/'+REV+'/'+quote(row['path'],safe='/'),out/Path(row['path']).name,4*2**20)
            assert len(data)==row['size']
            if 'lfs' in row:assert hashlib.sha256(data).hexdigest()==row['lfs']['oid']
            else:assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==row['oid']
        report['completed']=True
    finally:
        report['wall_seconds']=time.monotonic()-start;report['total_response_bytes']=total;save(out/'collection.json',report)
    print(json.dumps(dict(completed=report['completed'],requests=len(requests),response_bytes=total)))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--fetch-worker':
        url,target,cap=sys.argv[2:];cap=int(cap)
        try:
            with urlopen(Request(url,headers={'User-Agent':'moe-research-bounded-audit/1'}),timeout=20) as response:
                status=response.status;data=response.read(cap+1)
            Path(target).write_bytes(data);assert len(data)<=cap
            print(json.dumps(dict(status=status)))
        except Exception as error:
            print(json.dumps(dict(error_type=type(error).__name__,http_error_code=getattr(error,'code',None))))
            sys.exit(1)
    else:main()
