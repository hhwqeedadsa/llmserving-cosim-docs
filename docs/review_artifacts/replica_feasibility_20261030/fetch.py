"""Bounded public-source retrieval, with one attempt per URL and no code execution."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import urllib.error
import urllib.parse
import urllib.request


def fetch(root, identity, url, pdf=False):
    if not re.fullmatch('[a-z0-9-]+',identity):
        raise ValueError('use a simple source id')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('only public HTTPS URLs without credentials')
    root.mkdir(parents=True,exist_ok=True)
    attempts = root/'requests'
    attempts.mkdir(exist_ok=True)
    previous = [json.loads(p.read_bytes()) for p in attempts.glob('*.json')]
    if len(previous)>=24 or any(p['requested_url']==url for p in previous):
        raise ValueError('URL already attempted or request count exhausted')
    used = sum(p.get('captured_bytes',0) for p in previous)
    maximum = 4*2**20 if pdf else 2*2**20
    if used+maximum>32*2**20:
        raise ValueError('cumulative retrieval budget exhausted')
    record = dict(id=identity,requested_url=url,status='started',
                  started_utc=datetime.now(timezone.utc).isoformat(),timeout_seconds=20,
                  total_deadline_seconds=20,
                  response_limit_bytes=maximum,attempt_limit=1,external_code_executed=False)
    path = attempts/f'{identity}.json'
    with path.open('x') as stream:
        json.dump(record,stream,indent=2)
    error = None
    def deadline(signum, frame):
        raise TimeoutError('public-source retrieval exceeded its total 20-second deadline')
    previous_handler = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 20)
    try:
        request = urllib.request.Request(url,headers={'User-Agent':'moe-ub-cosim-research/1.0'})
        with urllib.request.urlopen(request,timeout=20) as response:
            record.update(http_status=response.status,final_url=response.url,
                          content_type=response.headers.get('Content-Type'),
                          etag=response.headers.get('ETag'),last_modified=response.headers.get('Last-Modified'))
            body = response.read(maximum+1)
        destination = root/(identity+('.pdf' if pdf else '.body'))
        with destination.open('xb') as stream:
            stream.write(body)
        record.update(captured_bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),local_path=str(destination))
        if len(body)>maximum:
            raise ValueError('response exceeds explicit limit; captured prefix retained')
        if pdf and not body.startswith(b'%PDF-'):
            raise ValueError('expected PDF signature')
        record['status'] = 'retrieved'
    except Exception as failure:
        record.update(status='failed',error_type=type(failure).__name__,error=str(failure))
        if isinstance(failure,urllib.error.HTTPError):
            record['http_status'] = failure.code
        error = failure
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        record['ended_utc'] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))
    if error:
        raise SystemExit(1)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--id',required=True)
    parser.add_argument('--url',required=True)
    parser.add_argument('--pdf',action='store_true')
    args = parser.parse_args()
    fetch(args.root.resolve(),args.id,args.url,args.pdf)
