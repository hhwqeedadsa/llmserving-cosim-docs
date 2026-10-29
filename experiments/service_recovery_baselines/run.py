"""Four registered public-CLI runs, using an existing binary and saved inputs.

Run under the registered outer host_probe, with its artifact root covering the
whole study. Stops at the first failure; never builds, retries, or edits old runs.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
CASES=(('fixed80-normal','bottleneck25',False),('karn80-normal','bottleneck25',True),
       ('fixed80-loss50','loss50',False),('karn80-loss50','loss50',True))


def read(path, maximum=8*2**20):
    if path.stat().st_size>maximum:raise ValueError('input exceeds byte limit: '+str(path))
    return json.loads(path.read_bytes())


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')


def run(baseline,backend,protoc,output):
    baseline,backend,protoc,output=map(lambda p:Path(p).resolve(),(baseline,backend,protoc,output))
    if output.exists():raise ValueError('output already exists')
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():
        raise ValueError('commit the frozen experiment before execution')
    if not backend.is_file() or not protoc.is_file():raise ValueError('existing executable/compiler required')
    output.mkdir(parents=True);prepared=output/'prepared';prepared.mkdir()
    cases=[]
    for name,old,adaptive in CASES:
        source=baseline/old;config=read(source/'input.json');protocol=read(source/'rdma.json')
        old_provenance=read(source/'provenance.json')
        if old_provenance['hashes'].get(str(backend))!=sha(backend):
            raise ValueError('backend differs from saved baseline binary')
        for row in config['routes']['requests']:
            path=(source/row['file']['path']).resolve()
            if not path.is_file() or path.stat().st_size>16*2**20 or sha(path)!=row['file']['sha256']:
                raise ValueError('saved source file differs')
            row['file']['path']=str(path)
        if protocol['recovery']['initial_rto_ns']!=40000 or 'rtt_estimator' in protocol['recovery']:
            raise ValueError('wrong reference recovery configuration')
        protocol['recovery']['initial_rto_ns']=80000
        if adaptive:protocol['recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=20000,granularity_ns=1)
        folder=prepared/name;folder.mkdir()
        save(folder/'service.json',config);save(folder/'rdma.json',protocol)
        save(folder/'topology.json',read(source/'topology.json'))
        cases.append(dict(name=name,reference=old,adaptive=adaptive,
            source_input_sha256=sha(source/'input.json'),source_topology_sha256=sha(source/'topology.json'),
            prepared_hashes={p.name:sha(p) for p in sorted(folder.iterdir())}))
    manifest=dict(schema_version='moe-service-recovery-baselines-preparation/1',
        platform_and_method_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        baseline_root=str(baseline),backend=str(backend),backend_sha256=sha(backend),
        protoc=str(protoc),protoc_sha256=sha(protoc),cases=cases,
        script_hashes={p.name:sha(p) for p in Path(__file__).parent.iterdir() if p.suffix in ('.py','.md')},
        network_runs_planned=4,builds=0,downloads=0)
    save(output/'preparation.json',manifest)
    env=dict(os.environ,PYTHONPATH=str(ROOT/'python'),PYTHONDONTWRITEBYTECODE='1')
    for case in cases:
        name=case['name'];folder=prepared/name
        command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','simai-run',
            '--config',str(folder/'service.json'),'--protocol',str(folder/'rdma.json'),
            '--topology',str(folder),'--runtime',str(baseline/'bottleneck25/simai_runtime'),
            '--backend',str(backend),'--protoc',str(protoc),'--output',str(output/name)]
        start=time.monotonic()
        with (output/(name+'.stdout.log')).open('x') as out,(output/(name+'.stderr.log')).open('x') as err:
            process=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=err,timeout=120)
        save(output/(name+'.command.json'),dict(command=command,cwd=str(ROOT),exit_code=process.returncode,
            wall_seconds=time.monotonic()-start))
        if process.returncode:raise RuntimeError('stopped after failed case '+name)
        result=read(output/name/'result.json',128*2**20)
        if not result['packet_capture']['complete'] or not result['recovery']['complete']:
            raise RuntimeError('stopped after incomplete evidence '+name)
        print(json.dumps(dict(case=name,passed=result['passed'],finish_ns=result['finish_ns'])),flush=True)
    save(output/'execution-complete.json',dict(network_runs=4,passed=True))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline-root','backend','protoc','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();run(args.baseline_root,args.backend,args.protoc,args.output)
