"""Prepare, execute and audit the registered 320-GPU experiment via public CLI."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
CASES=('clos-normal','clos-loss','hpn-normal','hpn-loss')


def read(p):return json.loads(p.read_bytes())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def command(root,label,args):
    logs=root/'commands';logs.mkdir(exist_ok=True)
    record=logs/(label+'.json');assert not record.exists(),label+' already executed'
    argv=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
    save(record,dict(argv=argv,cwd=str(REPO),status='started'))
    with (logs/(label+'.stdout')).open('w') as out,(logs/(label+'.stderr')).open('w') as err:
        completed=subprocess.run(argv,cwd=REPO,stdout=out,stderr=err,timeout=90)
    save(record,dict(argv=argv,cwd=str(REPO),exit_code=completed.returncode))
    assert completed.returncode==0,str(record)
    return read(logs/(label+'.stdout')) if (logs/(label+'.stdout')).read_text().lstrip().startswith('{') else None


def prepare(root):
    assert not root.exists();root.mkdir(parents=True);inputs=root/'inputs';inputs.mkdir();sources=inputs/'routes';sources.mkdir()
    intake_path=REPO/'experiments/route_coverage/results/local_additions_20261027/intake.json';intake=read(intake_path)
    entries=sorted((v for v in intake['files'] if v['eligible_offline']),key=lambda v:int(Path(v['name']).stem));assert len(entries)==15
    requests=[];records=[]
    for i,item in enumerate(entries):
        source=REPO/'data/moe-trace-small'/item['name'];assert sha(source)==item['sha256']
        target=sources/item['name'];shutil.copyfile(source,target)
        group,local,size=(0,i,256) if i<12 else (1,i-12,64)
        rank=local*size//(12 if group==0 else 3)+(0 if group==0 else 256)
        requests.append(dict(rank_id=rank,slot=0,file_id='local-'+Path(item['name']).stem,
                             file=dict(path='routes/'+item['name'],sha256=sha(target)),first_decode_entry=1))
        records.append(dict(name=item['name'],sha256=sha(target),bytes=target.stat().st_size,rank_id=rank,
                            original_path=item['original_path'],download_revision=None,remote_identity_verified=False))
    c=read(REPO/'examples/simai_service/service.json');c['service']['model']['layers']=2
    c['service']['workload']['context_tokens']=2048;c['service']['parallel']['groups']=[list(range(256)),list(range(256,320))]
    c['simai']['max_sequence_tokens']=4096
    c['routes'].update(layer_ids=[3,4],requests=requests,source=dict(kind='user_provided_model_routes',
        dataset_id=intake['source']['dataset_id'],collector_model_id=intake['source']['collector_model_id'],
        directory=intake['source']['confirmed_directory'],download_revision=None,remote_identity_verified=False))
    save(inputs/'service.json',c);save(inputs/'sources.json',dict(intake_sha256=sha(intake_path),source=intake['source'],files=records))
    protocol=read(REPO/'examples/simai_service/rdma.json')
    protocol['ecn_by_rate']['200000000000']=dict(kmin_bytes=65536,kmax_bytes=262144,pmax=1.0)
    protocol['limits'].update(max_gpus=320,max_switches=128,max_links=2048,max_messages=4096,artifact_bytes=256*2**20)
    save(inputs/'normal.json',protocol)
    plans={}
    for family,filename in (('clos','clos-320.json'),('hpn','hpn-inspired-320.json')):
        shutil.copyfile(REPO/'examples/scaleout_large_models'/filename,inputs/(family+'-topology.json'))
        command(root,'topology-'+family,['topology','--config',inputs/(family+'-topology.json'),'--output',root/('topology-'+family)])
        topology=read(root/('topology-'+family)/'topology.json');nodes={v['id']:v for v in topology['nodes']}
        matrix=Counter()
        for request in requests[:12]:
            raw=read(inputs/request['file']['path'])
            for expert in raw[1]['3'][0]:matrix[request['rank_id'],expert]+=1
        selected=[]
        for (src,dst),count in matrix.items():
            if nodes[src]['pod']!=nodes[dst]['pod']:
                # g0.w0/layer0/Dispatch stable pair identity, independent of results.
                identity=src*255+(dst if dst<src else dst-1)
                selected.append(dict(id=identity,src=src,dst=dst,bytes=count*14336))
        selected.sort(key=lambda v:(-v['bytes'],v['id']));a,b=selected[:2]
        loss=json.loads(json.dumps(protocol));loss['faults']=[
            dict(node=a['dst'],port=0,message_id=a['id'],kind='data',sequence=9000,start_ns=0,end_ns=5000000,count=2),
            dict(node=b['src'],port=0,message_id=b['id'],kind='ack',sequence=b['bytes'],start_ns=0,end_ns=5000000,count=1)]
        save(inputs/(family+'-loss.json'),loss);plans[family]=dict(data_message=a,ack_message=b,selection='input_only_before_network_execution')
        command(root,'preflight-'+family,['rdma-preflight','--topology',root/('topology-'+family),'--protocol',inputs/'normal.json','--output',root/(family+'-preflight.json')])
    save(inputs/'fault-selection.json',plans)
    save(root/'preparation.json',dict(passed=True,platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        source_requests=15,source_bytes=sum(v['bytes'] for v in records),physical_gpus=320,groups=[256,64],
        input_hashes={str(p.relative_to(root)):sha(p) for p in inputs.rglob('*') if p.is_file()}))


def execute(root,runtime,backend,protoc,cases):
    assert read(root/'preparation.json')['passed']
    for case in cases:assert not (root/case).exists(),case+' already exists; never overwrite or rerun a successful condition'
    save(root/'execution-inputs.json',dict(runtime=str(runtime),backend=str(backend),backend_sha256=sha(backend),protoc=protoc))
    for case in cases:
        family,condition=case.split('-');protocol=root/'inputs'/('normal.json' if condition=='normal' else family+'-loss.json')
        command(root,'run-'+case,['simai-run','--config',root/'inputs/service.json','--topology',root/('topology-'+family),
            '--protocol',protocol,'--runtime',runtime,'--backend',backend,'--protoc',protoc,'--output',root/case])
        print('completed '+case,flush=True)


def audit(root):
    summaries={}
    for case in CASES:
        checked=command(root,'check-'+case,['simai-check','--run',root/case])
        r=read(root/case/'result.json');assert r['packet_capture']['complete']
        if case.endswith('loss'):assert r['fault_injection']['drop_counts']==[2,1] and r['fault_drops']==3
        else:assert r['fault_drops']==0
        for phase in ('dispatch','combine'):
            command(root,'arrivals-'+case+'-'+phase,['rdma-arrivals','--run',root/case,'--graph','g0.w0','--layer','0',
                '--phase',phase,'--output',root/(case+'-'+phase+'-audit')])
        summaries[case]=dict(verification=checked,packet_events=len(r['packet_capture']['events']),
            finish_ns=r['finish_ns'],fault_drops=r['fault_drops'],host=read(root/case/'execution/probe.json'),
            dispatch=read(root/(case+'-dispatch-audit')/'summary.json'),combine=read(root/(case+'-combine-audit')/'summary.json'))
    for family in ('clos','hpn'):
        a,b=(summaries[family+'-'+condition] for condition in ('normal','loss'))
        assert a['verification']['remote_payload_bytes']==b['verification']['remote_payload_bytes']
        for graph in ('g0.w0','g0.w1','g1.w0','g1.w1'):
            assert sha(root/(family+'-normal')/'graphs'/graph/'wave.json')==sha(root/(family+'-loss')/'graphs'/graph/'wave.json')
    save(root/'summary.json',summaries)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=('prepare','run','audit'));p.add_argument('--root',type=Path,required=True)
    p.add_argument('--runtime',type=Path);p.add_argument('--backend',type=Path);p.add_argument('--protoc',default='protoc')
    p.add_argument('--case',choices=CASES,action='append');a=p.parse_args();root=a.root.resolve()
    if a.stage=='prepare':prepare(root)
    elif a.stage=='audit':audit(root)
    else:
        assert a.runtime and a.backend,'--runtime and --backend required'
        execute(root,a.runtime.resolve(),a.backend.resolve(),a.protoc,a.case or CASES)
