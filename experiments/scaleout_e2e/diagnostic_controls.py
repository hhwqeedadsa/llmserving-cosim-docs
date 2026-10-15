"""Separate diagnostic: suppress expert compute in both balanced/hot runs.

This changes the reference regime; it is not an additive decomposition of the
main experiment's latency. Only public simulation CLI is used.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-run',type=Path,required=True);p.add_argument('--backend-json',type=Path,required=True)
    p.add_argument('--protoc',required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(exist_ok=False)
    source=json.loads((args.baseline_run/'input.json').read_text());backend=json.loads(args.backend_json.read_text())
    records=[]
    for name,hot in [('balanced',0),('hotspot',0.5)]:
        config=deepcopy(source);config['workload']['hotspot_fraction']=hot
        config['compute']['expert_ns_per_contribution']=0
        path=out/f'{name}.json';path.write_text(json.dumps(config,indent=2)+'\n')
        command=[sys.executable,'-m','moe_ub_cosim','scaleout','rdma-run','--config',str(path),
            '--topology',str(args.baseline_run.resolve()),'--protocol',str((args.baseline_run/'rdma.json').resolve()),
            '--backend',backend['service'],'--protoc',args.protoc,'--output',str(out/name)]
        with (out/f'{name}.stdout.log').open('w') as stdout,(out/f'{name}.stderr.log').open('w') as stderr:
            result=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=90)
        records.append(dict(name=name,argv=command,exit_code=result.returncode))
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if result.returncode:raise RuntimeError('diagnostic stopped; inspect '+name+'.stderr.log')
    results={name:json.loads((out/name/'verification.json').read_text()) for name in ('balanced','hotspot')}
    assert results['balanced']['remote_payload_bytes']==results['hotspot']['remote_payload_bytes']
    (out/'summary.json').write_text(json.dumps(dict(results=results,expert_cost_ns=0,route_seed=source['workload']['route_seed'],
        limit='different reference regime; do not subtract this difference from the main result'),indent=2)+'\n')


if __name__=='__main__':main()
