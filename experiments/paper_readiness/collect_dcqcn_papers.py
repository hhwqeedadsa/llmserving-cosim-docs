"""Bounded primary-paper collection; no downloaded code execution or retries."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request,urlopen

SOURCES={
    'dcqcn2015':'https://conferences.sigcomm.org/sigcomm/2015/pdf/papers/p523.pdf',
    'roce_hoti2022':'https://arxiv.org/pdf/2207.10898',
}


def save(path,value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def fetch(key,out):
    start=time.monotonic()
    record=dict(id=key,url=SOURCES[key],accessed_utc=datetime.now(timezone.utc).isoformat())
    try:
        with urlopen(Request(SOURCES[key],headers={'User-Agent':'MoE-model-validity-review'}),timeout=15) as response:
            record.update(http_status=response.status,resolved_url=response.url)
            data=response.read(4*2**20+1)
        assert len(data)<=4*2**20,'primary PDF exceeds4MiB'
        assert data.startswith(b'%PDF'),'response is not a PDF'
        (out/(key+'.pdf')).write_bytes(data)
        record.update(status='read',bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    except Exception as error:
        record.update(status='unavailable',error=type(error).__name__,reason=str(error))
    finally:
        record['wall_seconds']=time.monotonic()-start
        save(out/(key+'-request.json'),record)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--fetch',choices=tuple(SOURCES))
    a=p.parse_args();out=a.output.resolve()
    if a.fetch:
        fetch(a.fetch,out);return
    assert not out.exists();out.mkdir()
    records=[]
    for key,url in SOURCES.items():
        start=time.monotonic()
        command=[sys.executable,str(Path(__file__).resolve()),'--output',str(out),'--fetch',key]
        with (out/(key+'.stdout')).open('w') as stdout,(out/(key+'.stderr')).open('w') as stderr:
            try:
                run=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=20)
                assert run.returncode==0,'collector subprocess failed'
                record=json.loads((out/(key+'-request.json')).read_text())
            except subprocess.TimeoutExpired:
                record=dict(id=key,url=url,status='unavailable',reason='hard20s request deadline',wall_seconds=time.monotonic()-start)
        record['command']=command
        if record['status']=='read':
            text=out/(key+'.txt')
            conversion=['pdftotext','-layout','-nopgbrk',str(out/(key+'.pdf')),str(text)]
            converted=subprocess.run(conversion,capture_output=True,text=True,timeout=10)
            record['conversion']=dict(command=conversion,exitcode=converted.returncode)
            (out/(key+'-conversion.stderr')).write_text(converted.stderr)
            assert converted.returncode==0 and text.stat().st_size<=4*2**20
            record.update(text_bytes=text.stat().st_size,text_sha256=hashlib.sha256(text.read_bytes()).hexdigest())
        records.append(record);save(out/'sources.json',dict(sources=records,downloaded_code_executed=False))
    print(json.dumps(records))


if __name__=='__main__':main()
