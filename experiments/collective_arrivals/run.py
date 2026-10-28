"""Run two bounded real-route service conditions and public endpoint arrival audits."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(path):return json.loads(path.read_bytes())
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def run(repo,output,backend,protoc):
    # Fail before preparing a study if the configured frontend interpreter is absent.
    import google.protobuf  # noqa: F401 -- generated pinned Chakra schema requires it.
    assert not output.exists();output.mkdir(parents=True)
    start=time.monotonic();commands=[]
    def cli(label,args):
        assert time.monotonic()-start < 600
        assert sum(p.stat().st_size for p in output.rglob('*') if p.is_file()) < 256*2**20
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        before=time.monotonic()
        with (output/(label+'.stdout')).open('w') as out,(output/(label+'.stderr')).open('w') as err:
            done=subprocess.run(cmd,cwd=repo,stdout=out,stderr=err,timeout=90)
        commands.append(dict(label=label,argv=cmd,cwd=str(repo),exit_code=done.returncode,wall_seconds=time.monotonic()-before))
        save(output/'commands.json',commands)
        if done.returncode:raise RuntimeError(label+' failed; stop without retry; inspect '+str(output/(label+'.stderr')))
    manifest_path=repo/'data/moe-trace-small/manifest.json';manifest=read(manifest_path)
    assert len(manifest['files'])==4 and manifest['model']['name']=='deepseek-ai/DeepSeek-R1'
    service=read(repo/'examples/scaleout_rdma/service.json')
    service['model'].update(name=manifest['model']['name'],hidden_size=7168,ffn_hidden_size=2048,
                            experts=256,top_k=8,layers=2)
    service['workload'].update(batch_per_rank=1,decode_steps=2,context_tokens=2048,hotspot_fraction=0)
    service['parallel']['groups']=[list(range(4))]
    service['route_replay']=dict(manifest=dict(path=str(manifest_path),sha256=sha(manifest_path)),
        layer_ids=[3,4],requests=[dict(rank_id=r,slot=0,file_id=f['file_id'],first_decode_entry=1)
                                for r,f in enumerate(manifest['files'])],budget=dict(max_files=4,max_bytes=32*2**20))
    # Freeze message selection from source choices before seeing any network result.
    candidates=[];hist=Counter()
    for src,f in enumerate(manifest['files']):
        path=repo/f['file']['path'];assert sha(path)==f['file']['sha256']
        raw=read(path);choices=raw[1]['3'][0];assert len(choices)==len(set(choices))==8
        hist.update(choices);counts=Counter(e//64 for e in choices)
        for dst,count in counts.items():
            if src//2 != dst//2:
                identity=src*3+(dst if dst<src else dst-1)
                candidates.append(dict(id=identity,src=src,dst=dst,bytes=count*7168*2))
    candidates.sort(key=lambda m:(-m['bytes'],m['id']));data,ack=candidates[:2]
    save(output/'selection.json',dict(slice=dict(graph='g0.w0',layer=0,source_layer=3,phase='dispatch'),
        data_target=data,ack_target=ack,expert_counts=dict(sorted(hist.items())),manifest_sha256=sha(manifest_path)))
    save(output/'service.json',service)
    topology=read(repo/'examples/scaleout_rdma/small-clos-100g.json')
    topology['deployment']['gpus_per_server']=1;topology['nic']['rails_by_gpu_slot']=[[0]]
    for tier,link in topology['links'].items():
        link['rate_bps']=100000000000 if tier=='access' else 25000000000
    save(output/'topology-config.json',topology)
    cli('topology',['topology','--config',output/'topology-config.json','--output',output/'topology'])
    protocol=read(repo/'examples/scaleout_rdma/rdma.json');protocol['schema_version']='moe-scaleout-rdma/2'
    protocol['payload_bytes']=2000
    protocol['ecn_by_rate']['25000000000']=deepcopy(protocol['ecn_by_rate']['100000000000'])
    protocol['limits']=dict(max_gpus=8,max_switches=16,max_links=128,max_messages=256,
        max_queue_events=200000,queue_capture='summary',max_sim_ns=5000000,wall_seconds=60,
        rss_bytes=2**30,address_bytes=2**31,artifact_bytes=128*2**20,result_json_bytes=32*2**20)
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000,wire_headers=True)
    protocol['queue_metrics']=True
    protocol['recovery']=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=4,max_events=100000)
    def rule(target,kind,sequence,count):
        return dict(node=target['dst'] if kind=='data' else target['src'],port=0,
            message_id=target['id'],kind=kind,sequence=sequence,count=count,start_ns=0,end_ns=5000000)
    all_summaries={}
    for name in ('normal','data-and-ack-loss'):
        p=deepcopy(protocol)
        if name!='normal':p['faults']=[rule(data,'data',2000,2),rule(ack,'ack',ack['bytes'],1)]
        save(output/(name+'-protocol.json'),p)
        cli(name,['rdma-run','--config',output/'service.json','--topology',output/'topology',
            '--protocol',output/(name+'-protocol.json'),'--backend',backend,'--protoc',protoc,'--output',output/name])
        for phase in ('dispatch','combine'):
            target=output/(name+'-'+phase+'-audit')
            cli(name+'-'+phase,['rdma-arrivals','--run',output/name,'--graph','g0.w0','--layer','0',
                '--phase',phase,'--output',target])
            all_summaries[name+'-'+phase]=read(target/'summary.json')
    identity=dict(backend=str(backend),backend_sha256=sha(backend),protoc=protoc,
        source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        source_dirty=True,network_runs=2,builds=0,gpu_runs=0)
    save(output/'identity.json',identity);save(output/'summary.json',all_summaries)
    return all_summaries


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','output','backend'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--protoc',required=True);a=p.parse_args()
    run(a.repository.resolve(),a.output.resolve(),a.backend.resolve(),a.protoc)
