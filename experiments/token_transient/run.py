"""Run one frozen condition through public CLI; refuse repeated attempts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from common import ROOT, HERE, load, sha, save, need


def run(root, receipt, protoc, case):
    need(not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),
         'commit method before network execution')
    prepared = root/'prepared'
    frozen = load(prepared/'preparation.json')
    need(frozen['gpu_endpoints'] == 128 and frozen['ep_sizes'] == [64,64], 'wrong frozen scale')
    for name,digest in frozen['input_hashes'].items():
        need(sha(prepared/name) == digest, 'changed input '+name)
    for name,digest in frozen['method_hashes'].items():
        need(sha(HERE/name) == digest, 'changed method '+name)
    backend = load(receipt)
    identities = {backend['service']:backend['service_sha256'],**backend['libraries']}
    identities.update({str(ROOT/name):digest for name,digest in backend['source_hashes'].items()})
    for name,digest in identities.items():
        need(sha(Path(name)) == digest, 'changed backend/library/source '+name)
    attempts = root/'attempts'
    attempts.mkdir(exist_ok=True)
    record = attempts/f'{case}.json'
    need(not record.exists() and not (root/case).exists(), 'condition already attempted')
    if case == 'local':
        need(load(attempts/'normal.json')['status'] == 'verified', 'normal must pass first')
    need(shutil.disk_usage(root).free >= 8*2**30, 'disk reserve exhausted')
    need(sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) < 512*2**20, 'task artifact budget exhausted')
    need(sum(load(p)['wall_seconds'] for p in (root/'monitors').glob('*/probe.json')) < 900, 'task time budget exhausted')
    prefix = [sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    command = prefix+['rdma-run','--config',str(prepared/'service.json'),
        '--protocol',str(prepared/f'{case}-rdma.json'),'--topology',str(prepared/'topology'),
        '--backend',backend['service'],'--protoc',str(protoc),'--output',str(root/case)]
    entry = dict(case=case,status='started',command=command,cwd=str(ROOT),
        backend_receipt_sha256=sha(receipt),protoc_sha256=sha(protoc),
        preparation_sha256=sha(prepared/'preparation.json'),
        execution_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
    save(record,entry)
    env = dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    start = time.monotonic()
    try:
        with (attempts/f'{case}.stdout').open('x') as out,(attempts/f'{case}.stderr').open('x') as err:
            code = subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err,timeout=90).returncode
        entry['exit_code'] = code
        need(code == 0, 'network execution failed; stop further conditions')
        graphs = sorted((root/case/'graphs').glob('*/wave.json'))
        need(len(graphs) == 4 and all(load(p) == load(prepared/'planned'/f'{p.parent.name}.json') for p in graphs),
             'actual graphs differ from frozen plan')
        checks = ['rdma-check']+(['data-loss-check'] if case == 'local' else [])
        for check in checks:
            with (attempts/f'{case}-{check}.json').open('x') as out:
                subprocess.run(prefix+[check,'--run',str(root/case)],cwd=ROOT,env=env,stdout=out,check=True,timeout=30)
        entry.update(status='verified',public_checks=checks,graphs_verified=True)
    except BaseException as error:
        entry.update(status='failed',error_type=type(error).__name__,error=str(error))
        raise
    finally:
        entry['wall_seconds'] = time.monotonic()-start
        record.write_text(json.dumps(entry,indent=2)+'\n')
    print(json.dumps(entry))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root','backend-receipt','protoc'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--case',choices=('normal','local'),required=True)
    args = parser.parse_args()
    run(args.root.resolve(),args.backend_receipt.resolve(),args.protoc.resolve(),args.case)
