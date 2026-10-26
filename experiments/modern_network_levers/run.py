"""Three preregistered full-modern calls, using public CLI only."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def load(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    for name in ('repository','stage','manifest','runtime','identity'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve();assert not stage.exists()
    stage.mkdir();(stage/'inputs').mkdir();(stage/'empty-cwd').mkdir()
    manifest=load(a.manifest);identity=load(a.identity)
    def check_runtime():
        assert all(sha(a.runtime/name)==digest for name,digest in identity['files'].items())
        assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())
    check_runtime();save(stage/'runtime-identity.json',identity)
    cfg=load(repo/'examples/modern_deepep_native/input-selective.json')
    x=cfg['modern']['execution'];x.update(ranks=4,max_tokens_per_rank=1,num_sms=2,dispatch_warps=2,
        combine_warps=2,call_contexts=5,requested_contexts=5,local_devices=[[0,1] for _ in range(4)])
    cfg['modern']['model'].update(experts=256,top_k=8,hidden_size=7168)
    source=[];tokens=[]
    for item in manifest['files']:
        path=repo/item['file']['path'];assert sha(path)==item['file']['sha256']
        choices=load(path)[2]['3'];assert len(choices)==1 and len(choices[0])==8 and len(set(choices[0]))==8
        tokens.append(choices);source.append(dict(file=item['file'],entry_index=2,layer=3,choices=choices))
    assert len(tokens)==4
    cfg['modern']['calls']=[dict(name='entry2.layer3',tokens=tokens)]
    device=deepcopy(cfg['deployment'][0]['devices'])
    cfg['deployment']=[dict(rank=rank,node=node,devices=deepcopy(device)) for rank,node in enumerate((0,1,4,5))]
    save(stage/'inputs/input.json',cfg)
    protocol=load(repo/'examples/modern_deepep_native/rdma.json');protocol['payload_bytes']=2000
    save(stage/'inputs/protocol.json',protocol)
    save(stage/'source.json',dict(platform_commit='710bafc',repository_head=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),route_manifest_sha256=sha(a.manifest),
        dataset_source=manifest['source'],selected_rows=source,runtime=str(a.runtime),
        input_sha256=sha(stage/'inputs/input.json'),protocol_sha256=sha(stage/'inputs/protocol.json')))
    save(stage/'inputs/manifest.json',manifest)
    records=[]
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    def run(label,argv,network=False):
        assert sum(row['network'] for row in records)+int(network)<=3
        probes=[load(path) for path in stage.rglob('probe.json')]
        assert sum(row['wall_seconds'] for row in probes)+65<450
        assert sum(path.stat().st_size for path in stage.rglob('*') if path.is_file())+2*2**20<256*2**20
        assert shutil.disk_usage(stage).free>8*2**30 and shutil.disk_usage(repo).free>64*2**20
        records.append(dict(label=label,command=list(map(str,argv)),cwd=str(stage/'empty-cwd'),network=network,state='started'))
        save(stage/'commands.json',records)
        monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(stage/(label+'-monitor')),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(96*2**20),
            '--artifact-root',str(stage),'--wall-seconds','60','--min-free-bytes',str(8*2**30),'--',*map(str,argv)]
        result=subprocess.run(monitor,cwd=stage/'empty-cwd',env=env,timeout=65)
        records[-1].update(state='terminal',exit_code=result.returncode);save(stage/'commands.json',records)
        assert result.returncode==0,'preserve failure and stop; never increase limits or rerun successful network'
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    for name in ('baseline','bandwidth','propagation'):
        topology=load(repo/'examples/modern_deepep_native/topology.json')
        if name=='bandwidth':
            for link in topology['links'].values():link['rate_bps']=200000000000
            topology['nic']['total_rate_bps']=400000000000
        if name=='propagation':
            for link in topology['links'].values():link['delay_ns']=500
        save(stage/'inputs'/(name+'-topology.json'),topology)
        proto=deepcopy(protocol)
        if name=='bandwidth':proto['ecn_by_rate']={'200000000000':next(iter(proto['ecn_by_rate'].values()))}
        save(stage/'inputs'/(name+'-protocol.json'),proto)
        run(name+'-topology',[*cli,'topology','--config',stage/'inputs'/(name+'-topology.json'),
            '--output',stage/(name+'-topology')])
        run(name,[*cli,'modern-deepep-native','--topology',stage/(name+'-topology'),
            '--protocol',stage/'inputs'/(name+'-protocol.json'),'--config',stage/'inputs/input.json',
            '--backend',a.runtime/'run-transaction-session','--output',stage/name,'--callback-batches'],True)
    check_runtime();save(stage/'execution.json',dict(passed=True,closed=True,actual_network_executions=3,
        runtime_unchanged=True,commands=records))


if __name__=='__main__':main()
