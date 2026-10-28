"""Registered severe-loss study, using public platform commands only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
CASES=('clean400','bottleneck25','loss50','loss75','blackout','persistent')


def read(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def budget(root):
    assert shutil.disk_usage(root).free>=8*2**30,'less than registered free reserve'
    size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    assert size<=3*2**30,'cumulative artifact budget exceeded'
    elapsed=sum(read(p).get('elapsed_seconds',0) for p in (root/'commands').glob('*.json'))
    assert elapsed<1200,'cumulative command time budget exceeded'


def command(root,label,args,expected_success=True):
    budget(root);logs=root/'commands';logs.mkdir(exist_ok=True);record=logs/(label+'.json')
    assert not record.exists(),label+' already attempted'
    argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
    save(record,dict(argv=argv,cwd=str(REPO),status='started'));start=time.monotonic()
    try:
        with (logs/(label+'.stdout')).open('w') as out,(logs/(label+'.stderr')).open('w') as err:
            r=subprocess.run(argv,cwd=REPO,stdout=out,stderr=err,timeout=90)
        code=r.returncode
    except subprocess.TimeoutExpired:
        save(record,dict(argv=argv,cwd=str(REPO),status='timeout',elapsed_seconds=time.monotonic()-start));raise
    save(record,dict(argv=argv,cwd=str(REPO),exit_code=code,elapsed_seconds=time.monotonic()-start))
    assert (code==0)==expected_success,str(record)
    if code==0:
        output=(logs/(label+'.stdout')).read_text()
        return json.loads(output) if output.lstrip().startswith('{') else None


def prepare(root):
    assert not root.exists();root.mkdir(parents=True);inputs=root/'inputs';inputs.mkdir();(inputs/'routes').mkdir()
    intake_path=REPO/'experiments/route_coverage/results/local_additions_20261027/intake.json'
    intake=read(intake_path);entries=sorted((v for v in intake['files'] if v['eligible_offline']),key=lambda v:int(Path(v['name']).stem))
    assert len(entries)==15;requests=[];sources=[]
    for i,item in enumerate(entries):
        source=REPO/'data/moe-trace-small'/item['name'];assert sha(source)==item['sha256']
        target=inputs/'routes'/item['name'];shutil.copyfile(source,target)
        rank,slot=(0,i) if i<6 else ((128,i-6) if i<12 else (256,i-12))
        requests.append(dict(rank_id=rank,slot=slot,file_id='local-'+source.stem,
            file=dict(path='routes/'+source.name,sha256=sha(target)),first_decode_entry=1))
        sources.append(dict(name=source.name,sha256=sha(target),bytes=target.stat().st_size,rank_id=rank,slot=slot))
    c=read(REPO/'examples/simai_service/service.json');s=c['service'];s['model']['layers']=2
    s['workload'].update(batch_per_rank=6,context_tokens=2048)
    s['parallel']['groups']=[list(range(256)),list(range(256,320))];c['simai']['max_sequence_tokens']=4096
    c['routes'].update(layer_ids=[3,4],requests=requests,source=dict(kind='user_provided_model_routes',
        dataset_id=intake['source']['dataset_id'],collector_model_id=intake['source']['collector_model_id'],
        directory=intake['source']['confirmed_directory'],download_revision=None,remote_identity_verified=False))
    save(inputs/'service.json',c);save(inputs/'sources.json',dict(intake_sha256=sha(intake_path),source=intake['source'],files=sources))
    p=read(REPO/'examples/simai_severe_loss/normal.json')
    p['limits'].update(max_gpus=320,max_switches=128,max_links=2048,max_messages=4096)
    p['ecn_by_rate']['25000000000']=dict(kmin_bytes=16384,kmax_bytes=65536,pmax=1.0)
    for family in ('400','25'):
        t=read(REPO/'examples/scaleout_large_models/clos-320.json')
        if family=='25':
            for tier in ('tor_agg','agg_core','tor_core'):t['links'][tier]['rate_bps']=25000000000
        save(inputs/('topology'+family+'.json'),t)
        command(root,'topology'+family,['topology','--config',inputs/('topology'+family+'.json'),'--output',root/('topology'+family)])
        save(inputs/'preflight.json',p)
        command(root,'preflight'+family,['rdma-preflight','--topology',root/('topology'+family),
            '--protocol',inputs/'preflight.json','--output',root/('preflight'+family+'.json')])
    for case in CASES:
        q=json.loads(json.dumps(p))
        if case in ('loss50','loss75','blackout','persistent'):
            n,d={'loss50':(1,2),'loss75':(3,4),'blackout':(1,1),'persistent':(1,1)}[case]
            q['data_loss']=dict(kind='periodic_destination_data/1',numerator=n,denominator=d,offset=0,
                start_ns=0,end_ns=5000000 if case=='persistent' else 100000,nodes=[])
        save(inputs/(case+'.json'),q)
    save(root/'preparation.json',dict(passed=True,platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        input_hashes={str(p.relative_to(root)):sha(p) for p in inputs.rglob('*') if p.is_file()},source_requests=15,groups=[256,64]))


def execute(root,runtime,backend,protoc,cases):
    assert read(root/'preparation.json')['passed']
    identity=dict(runtime=str(runtime),backend=str(backend),backend_sha256=sha(backend),protoc=protoc)
    record=root/'execution-inputs.json'
    if record.exists():assert read(record)==identity
    else:save(record,identity)
    for case in cases:
        assert not (root/case).exists(),case+' already attempted'
        command(root,'run-'+case,['simai-run','--config',root/'inputs/service.json',
            '--topology',root/('topology400' if case=='clean400' else 'topology25'),
            '--protocol',root/'inputs'/(case+'.json'),'--runtime',runtime,'--backend',backend,
            '--protoc',protoc,'--output',root/case],expected_success=case!='persistent')
        r=read(root/case/'result.json')
        assert r['packet_capture']['complete']
        if case=='persistent':assert r['failure']['reason']=='recovery_retry_exhausted'
        print(json.dumps(dict(case=case,passed=r['passed'],finish_ns=r['finish_ns'],data_loss=r.get('data_loss'))),flush=True)


def audit(root):
    for case in CASES:
        if case!='persistent':command(root,'check-'+case,['simai-check','--run',root/case])
        if case not in ('clean400','bottleneck25'):command(root,'loss-check-'+case,['data-loss-check','--run',root/case])
        phases=['dispatch'] if case=='persistent' else ['dispatch','combine']
        for phase in phases:
            command(root,'arrivals-'+case+'-'+phase,['rdma-arrivals','--run',root/case,'--graph','g0.w0',
                '--layer','0','--phase',phase,'--output',root/(case+'-'+phase+'-audit')])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('prepare','run','audit'));p.add_argument('--root',type=Path,required=True)
    p.add_argument('--runtime',type=Path);p.add_argument('--backend',type=Path);p.add_argument('--protoc',default='protoc')
    p.add_argument('--case',choices=CASES,action='append');a=p.parse_args();root=a.root.resolve()
    if a.stage=='prepare':prepare(root)
    elif a.stage=='audit':audit(root)
    else:
        assert a.runtime and a.backend
        execute(root,a.runtime.resolve(),a.backend.resolve(),a.protoc,a.case or CASES)
