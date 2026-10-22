"""Bounded primary-source discovery; no installs, retries or downloaded execution."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlencode


def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',action='append',help='NAME=URL, for precise links obtained from primary discovery; one request each')
    a=p.parse_args();out=a.output.resolve()
    assert not out.exists() and shutil.disk_usage(out.parent).free>=8*2**30+16*2**20
    if a.source:
        sources=[s.split('=',1) for s in a.source]
        assert len(sources)<=3 and len({s[0] for s in sources})==len(sources)
        assert all(Path(name).name==name and url.startswith('https://') for name,url in sources)
    else:
        query=urlencode(dict(search_query='ti:"Exploiting inter-layer expert affinity"',start=0,max_results=3))
        sources=[('neurips2022.html','https://proceedings.neurips.cc/paper_files/paper/2022'),
                 ('exflow-arxiv.xml','https://export.arxiv.org/api/query?'+query)]
    out.mkdir();start=time.monotonic()
    def fetch(pair):
        name,url=pair;path=out/name;tick=time.monotonic()
        command=['curl','--silent','--show-error','--location','--max-time','20','--max-filesize','2097152',
                 '--output',str(path),'--write-out','%{http_code}',url]
        save(out/(name+'.command.json'),dict(command=command,cwd=str(Path.cwd())))
        with (out/(name+'.stderr.log')).open('w') as log:
            result=subprocess.run(command,stdout=subprocess.PIPE,stderr=log,text=True,timeout=22)
        row=dict(name=name,url=url,exit_code=result.returncode,http_status=result.stdout,wall_seconds=time.monotonic()-tick,
                 bytes=path.stat().st_size if path.exists() else 0)
        if path.exists():row['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        save(out/(name+'.request.json'),row);return row
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(fetch,sources))
    assert sum(r['bytes'] for r in rows)<=16*2**20
    save(out/'collection.json',dict(requests=rows,wall_seconds=time.monotonic()-start,retried=False,
        per_response_bytes=2*2**20,per_request_seconds=20,downloaded_code_executed=False))
    print(json.dumps(dict(requests=len(rows),successful=sum(r['exit_code']==0 and r['http_status']=='200' for r in rows))))


if __name__=='__main__':main()
