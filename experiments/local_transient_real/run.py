"""Run one frozen case with public CLI, retaining every attempt and failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
CASES=('gbn-normal','gbn-local','sr-normal','sr-local')


def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):
    with p.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def run(root,receipt,protoc,case):
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('freeze and commit method before network execution')
    prepared=root/'prepared';manifest=load(prepared/'preparation.json')
    for name,digest in manifest['input_hashes'].items():
        if sha(prepared/name)!=digest:raise ValueError('frozen input differs: '+name)
    for name,digest in manifest['method_hashes'].items():
        if sha(Path(__file__).parent/name)!=digest:raise ValueError('frozen method differs: '+name)
    binding=load(receipt)
    for key in ('binary_hashes','library_hashes'):
        for name,digest in binding[key].items():
            if sha(Path(name))!=digest:raise ValueError('binary/library receipt differs: '+name)
    backend=next(Path(name) for name in binding['binary_hashes'] if name.endswith('/ns3.42-moe-rdma-service'))
    if not protoc.is_file():raise ValueError('existing protoc required')
    attempts=root/'attempts';attempts.mkdir(exist_ok=True)
    record=attempts/(case+'.json')
    if record.exists() or (root/case).exists():raise ValueError('case already attempted')
    for previous in CASES[:CASES.index(case)]:
        state=load(attempts/(previous+'.json'))
        if state.get('exit_code')!=0 or state.get('passed') is not True or state.get('graphs_verified') is not True:
            raise ValueError('earlier case failed or unverified')
    if shutil.disk_usage(root).free<8*2**30:raise ValueError('free reserve exhausted')
    monitors=list((root/'monitors').glob('*/probe.json'))
    if sum(load(p)['wall_seconds'] for p in monitors)>=900:raise ValueError('cumulative time exhausted')
    if sum(p.stat().st_size for p in root.rglob('*') if p.is_file())>=512*2**20:raise ValueError('artifact budget exhausted')
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','rdma-run',
        '--config',str(prepared/(case+'-service.json')),'--protocol',str(prepared/(case+'-protocol.json')),
        '--topology',str(prepared/'topology'),'--backend',str(backend),'--protoc',str(protoc),'--output',str(root/case)]
    save(record,dict(status='started',case=case,command=command,cwd=str(ROOT),receipt_sha256=sha(receipt),
        backend_sha256=sha(backend),protoc=str(protoc),protoc_sha256=sha(protoc),
        preparation_sha256=sha(prepared/'preparation.json'),
        execution_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()))
    start=time.monotonic()
    try:
        with (attempts/(case+'.stdout')).open('x') as out,(attempts/(case+'.stderr')).open('x') as err:
            completed=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),
                PYTHONDONTWRITEBYTECODE='1'),stdout=out,stderr=err,timeout=90)
    except BaseException as error:
        entry=load(record);entry.update(status='execution_error',error=type(error).__name__,
            elapsed_seconds=time.monotonic()-start,graphs_verified=False)
        record.write_text(json.dumps(entry,indent=2)+'\n');raise
    entry=load(record);entry.update(exit_code=completed.returncode,elapsed_seconds=time.monotonic()-start,
        status='native_terminal',graphs_verified=False)
    result_path=root/case/'result.json'
    if result_path.is_file():
        result=load(result_path);entry.update(passed=result['passed'],finish_ns=result['finish_ns'],failure=result.get('failure'))
    record.write_text(json.dumps(entry,indent=2)+'\n')
    if completed.returncode or entry.get('passed') is not True:raise RuntimeError('failed case retained; stop subsequent network')
    for path in (root/case/'graphs').glob('*/wave.json'):
        if load(path)!=load(prepared/'planned'/(path.parent.name+'.json')):raise ValueError('actual graph differs from plan')
    if len(list((root/case/'graphs').glob('*/wave.json')))!=4:raise ValueError('missing actual graph')
    entry.update(status='verified',graphs_verified=True);record.write_text(json.dumps(entry,indent=2)+'\n')
    print(json.dumps(entry))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','receipt','protoc'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--case',choices=CASES,required=True);a=p.parse_args()
    run(a.root.resolve(),a.receipt.resolve(),a.protoc.resolve(),a.case)
