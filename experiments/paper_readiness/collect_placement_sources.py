"""Read bounded primary placement/model sources once; do not execute downloaded code."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+16*2**20
    out.mkdir();start=time.monotonic()
    def fetch(pair):
        name,url=pair;file=out/name
        cmd=['curl','--silent','--show-error','--location','--max-time','20','--max-filesize','2097152',
            '--output',str(file),'--write-out','%{http_code}',url]
        save(out/(name+'.command.json'),dict(command=cmd,cwd=str(Path.cwd())))
        tick=time.monotonic()
        with (out/(name+'.stderr.log')).open('w') as err:
            result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=err,text=True,timeout=22)
        row=dict(file=name,url=url,exit_code=result.returncode,http_status=result.stdout,
            wall_seconds=time.monotonic()-tick,bytes_retained=file.stat().st_size if file.exists() else 0)
        if file.exists():row['sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
        save(out/(name+'.request.json'),row);return row
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(fetch,[('moetuner-v1.html','https://arxiv.org/html/2502.06643v1'),
            ('model.json','https://huggingface.co/api/models/cognitivecomputations/DeepSeek-R1-AWQ')]))
    save(out/'collection.json',dict(requests=rows,downloaded_code_executed=False))
    model=next(r for r in rows if r['file']=='model.json')
    if model['exit_code']==0 and model['http_status']=='200':
        revision=json.loads((out/'model.json').read_text())['sha'];assert re.fullmatch('[0-9a-f]{40}',revision)
        rows.append(fetch(('config.json','https://huggingface.co/cognitivecomputations/DeepSeek-R1-AWQ/resolve/'+revision+'/config.json')))
    assert sum(r['bytes_retained'] for r in rows)<=16*2**20
    save(out/'collection.json',dict(requests=rows,wall_seconds=time.monotonic()-start,
        downloaded_code_executed=False,per_request_cap_bytes=2*2**20,per_request_wall_seconds=20,
        total_cap_bytes=16*2**20,retried=False,model_revision_not_trace_collector_revision=True))
    print(json.dumps(dict(requests=len(rows),successful=sum(r['exit_code']==0 and r['http_status']=='200' for r in rows))))


if __name__=='__main__':main()
