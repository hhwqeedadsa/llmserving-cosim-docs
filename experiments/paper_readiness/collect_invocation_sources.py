"""Read six fixed small DeepEP sources once; verify against the cached Git tree.

Fetched source is data only. This collector never imports or executes it.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
COMMIT='f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
FILES=('deep_ep/buffers/ep.py','csrc/kernels/ep/dispatch.hpp','csrc/kernels/ep/combine.hpp',
       'csrc/comm/stream.hpp','csrc/utils/event.hpp','deep_ep/utils/event.py')
CHILD='''from urllib.request import Request,urlopen
import sys
with urlopen(Request(sys.argv[1],headers={'User-Agent':'MoE-invocation-source-audit'}),timeout=18) as r:
 data=r.read(2097153)
 if len(data)>2097152:raise RuntimeError('response exceeds 2MiB')
 sys.stdout.buffer.write(data)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve()
    assert not out.exists()
    tree_path=ROOT/'data/research/deepep_modern_20261022/tree.json'
    tree=json.loads(tree_path.read_text());assert tree['sha']==COMMIT and not tree['truncated']
    blobs={r['path']:r for r in tree['tree'] if r['type']=='blob'}
    out.mkdir();(out/'sources').mkdir();(out/'responses').mkdir()
    def once(name):
        blob=blobs[name];assert blob['size']<2*2**20
        url=f'https://raw.githubusercontent.com/deepseek-ai/DeepEP/{COMMIT}/{name}'
        started=time.monotonic();row=dict(path=name,commit=COMMIT,url=url,git_blob_sha=blob['sha'],expected_bytes=blob['size'])
        try:
            r=subprocess.run([sys.executable,'-c',CHILD,url],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
            (out/'responses'/(name.replace('/','__')+'.stderr')).write_bytes(r.stderr)
            raw=r.stdout;(out/'responses'/(name.replace('/','__')+'.body')).write_bytes(raw)
            row.update(exit_code=r.returncode,response_bytes=len(raw))
            assert r.returncode==0, r.stderr.decode(errors='replace')[-500:]
            identity=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
            assert len(raw)==blob['size'] and identity==blob['sha']
            raw.decode('utf-8')
            dest=out/'sources'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            row.update(status='verified',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        except subprocess.TimeoutExpired as e:
            raw=e.stdout or b'';(out/'responses'/(name.replace('/','__')+'.partial')).write_bytes(raw)
            row.update(status='timeout',error=str(e),response_bytes=len(raw))
        except Exception as e:row.update(status='failed',error=str(e))
        row['wall_seconds']=time.monotonic()-started
        return row
    with ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(once,FILES))
    assert sum(r.get('response_bytes',0) for r in rows)<=12*2**20
    assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<=2**20
    manifest=dict(commit=COMMIT,tree=str(tree_path.relative_to(ROOT)),tree_sha256=hashlib.sha256(tree_path.read_bytes()).hexdigest(),
        sources=rows,requests=6,requests_per_url=1,executed_downloaded_code=False,
        complete=all(r['status']=='verified' for r in rows))
    (out/'sources.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(complete=manifest['complete'],sources=[dict(path=r['path'],status=r['status']) for r in rows])))
    if not manifest['complete']:raise SystemExit(1)


if __name__=='__main__':main()
