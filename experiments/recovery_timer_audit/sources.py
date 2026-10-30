"""Import checked public responses or acquire the four fixed official sources.

fetch writes curl response bodies with no retries; import validates API Git blobs.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
CATALOG=[('rxe_comp','https://api.github.com/repos/torvalds/linux/contents/drivers/infiniband/sw/rxe/rxe_comp.c?ref=v6.12','moe-rxe-source-response-20261030.json','d48af218074588cfb7c16594d162e62b4174f7d1'),
 ('rxe_qp','https://api.github.com/repos/torvalds/linux/contents/drivers/infiniband/sw/rxe/rxe_qp.c?ref=v6.12','moe-rxe-qp-response-20261030.json','d2f7b5195c19dd2df34f3c24f3031e946804eb6d'),
 ('rxe_req','https://api.github.com/repos/torvalds/linux/contents/drivers/infiniband/sw/rxe/rxe_req.c?ref=v6.12','moe-rxe-req-response-20261030.json','479c07e6e4ed3eab4646400455897cd20e3316a2'),
 ('rfc8985','https://www.rfc-editor.org/rfc/rfc8985.txt','moe-rfc8985-20261030.txt',None)]


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('fetch','import'))
    p.add_argument('--output',type=Path,required=True);p.add_argument('--acquired',type=Path)
    p.add_argument('--rfc6298',type=Path,default=ROOT/'data/research/paper_readiness_20261022/rfc6298-text.txt')
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    if a.mode=='fetch':
        assert a.acquired is None
        records=[]
        for name,url,file,blob in CATALOG:
            r=subprocess.run(['curl','--silent','--show-error','--fail','--connect-timeout','4',
              '--max-time','10','--max-filesize','200000','-L',url,'-o',str(out/file)],capture_output=True,text=True)
            records.append(dict(url=url,response=file,exit_code=r.returncode,error=r.stderr))
            (out/'requests.json').write_text(json.dumps(records,indent=2)+'\n')
            if r.returncode:raise SystemExit('source request failed; preserved, no retry')
        print(json.dumps(dict(acquired=True,requests=4,network_simulations=0)))
        return
    assert a.acquired
    records=[]
    for name,url,file,blob in CATALOG:
        source=a.acquired/file;assert source.stat().st_size<=200000
        if blob:
            response=json.loads(source.read_bytes());assert response['sha']==blob and response['encoding']=='base64'
            raw=base64.b64decode(response['content']);assert len(raw)==response['size']
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==blob
            suffix='.c';reference='https://github.com/torvalds/linux/blob/v6.12/'+response['path']
        else:
            raw=source.read_bytes();assert b'RACK-TLP' in raw and b'8985' in raw;suffix='.txt';reference=url
        target=out/(name+suffix);target.write_bytes(raw)
        records.append(dict(name=name,url=url,reference=reference,git_blob_sha1=blob,path=target.name,
          sha256=sha(target),bytes=len(raw),response_sha256=sha(source)))
    shutil.copyfile(a.rfc6298,out/'rfc6298.txt')
    records.append(dict(name='rfc6298',url='https://www.rfc-editor.org/rfc/rfc6298.txt',
      reference='https://www.rfc-editor.org/rfc/rfc6298#section-5',path='rfc6298.txt',sha256=sha(out/'rfc6298.txt'),
      bytes=(out/'rfc6298.txt').stat().st_size,provenance='previously acquired local RFC snapshot; not a new HTTP request'))
    assert sum(r['bytes'] for r in records)<2*2**20
    manifest=dict(passed=True,sources=records,original_preflight_requests=5,
      preflight_deadline_upper_bound_seconds=53,
      retained_request_failure=dict(url='https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/infiniband/sw/rxe/rxe_comp.c',
        method='HEAD',exit_code=28,deadline_seconds=15,error='Operation timed out after15002ms with0bytes',same_URL_retried=False),
      note='API was a different official retrieval endpoint; four bodies imported without new requests')
    (out/'sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(dict(imported=True,sources=len(records),bytes=sum(r['bytes'] for r in records))))


if __name__=='__main__':main()
