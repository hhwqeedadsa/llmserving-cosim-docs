"""Prepare five declared conditions through public topology and immutable inputs."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

BASE='2cfbaf83870ef9319832f9965d3aae1b5218fe12'
BACKEND='f62d940c040f41a7be2b24419c894023da7ce5b60d4861528affc48b7a178f69'
ROOT=Path(__file__).resolve().parents[2]

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,value):p.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('stage','backend','libraries'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();s=a.stage.resolve();s.mkdir(exist_ok=False)
    assert sha(a.backend)==BACKEND
    libs=sorted(a.libraries.resolve().glob('libns3*.so'));assert len(libs)==8
    sources={}
    for file in sorted((ROOT/'backend/rdma').iterdir()):
        if file.suffix in ('.hh','.cc'):sources[str(file.relative_to(ROOT))]=sha(file)
    for rel in ('python/moe_ub_cosim/scaleout_transactions.py','python/moe_ub_cosim/transaction_session.py'):
        sources[rel]=sha(ROOT/rel)
    for rel,digest in sources.items():assert hashlib.sha256(subprocess.check_output(['git','show',BASE+':'+rel],cwd=ROOT)).hexdigest()==digest
    (s/'inputs').mkdir()
    spec=json.loads((ROOT/'examples/scaleout_rdma/small-clos-100g.json').read_text());save(s/'inputs/topology-config.json',spec)
    command=[sys.executable,'-B','-m','moe_ub_cosim','scaleout','topology','--config',str(s/'inputs/topology-config.json'),'--output',str(s/'inputs/generated-topology')]
    done=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=20)
    save(s/'topology-command.json',dict(command=command,cwd=str(ROOT),exitcode=done.returncode,stdout=done.stdout,stderr=done.stderr));assert done.returncode==0
    topo=json.loads((s/'inputs/generated-topology/topology.json').read_text());slow=deepcopy(topo)
    changed=[l for l in slow['links'] if l['tier']=='access' and 0 in (l['src'],l['dst'])];assert len(changed)==1
    changed[0]['delay_ns']=250000
    save(s/'inputs/fast-topology.json',topo);save(s/'inputs/late-topology.json',slow)
    protocol=json.loads((ROOT/'examples/scaleout_rdma/rdma.json').read_text())
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=10000,wire_headers=True)
    save(s/'inputs/protocol.json',protocol)
    initial=dict(schema_version='moe-rdma-transactions/4',
        local_model=dict(mode='serial_per_gpu/1',duration_source='declared_ns'),
        source_model=dict(capture='registered_window_at_publish/1',release='transaction_completed/1'),
        local_memory_model=dict(mode='guarded_copy/1',acquire='admission/1',capture='start/1',commit='completion/1'),
        buffer_model=dict(mode='bounded_copied_put/1',transport_bytes_per_source=40,registered_bytes_per_owner=32,target_cache_bytes_per_owner=80,local_copy_bytes_per_owner=8),
        transport_recovery=dict(mode='go_back_n/1',initial_rto_ns=20000,max_rto_ns=20000,max_retries=2,max_events=1000,on_exhausted='isolate_stream'),
        connections=[dict(id=i,src=i,dst=4,plane=0,request_port=24000+2*i,response_port=24001+2*i,source_nic=1,destination_nic=1,queue_depth=2) for i in (0,1)],
        windows=[dict(owner=i,key=7,address=4096,allow_atomic=False,hex=('%02x'%(0x11 if i==0 else 0x22))*8) for i in (0,1)]+[dict(owner=4,key=9,address=8192,allow_atomic=False,hex='00'*32)],operations=[])
    save(s/'inputs/initial.json',initial)
    cases=[]
    for name in ('fast-shared','late-shared','late-isolated','late-copy-out','late-receive-order'):
        mode=name.split('-',1)[1]
        policy=dict(mode=mode,deadline_ns=100000,new_address=8200 if mode=='isolated' else 8192,
                    output_address=8208 if mode=='copy-out' else 8200 if mode=='isolated' else 8192)
        save(s/('inputs/'+name+'.json'),policy)
        cases.append(dict(name=name,policy='inputs/'+name+'.json',topology='inputs/'+('fast' if name.startswith('fast-') else 'late')+'-topology.json',expected_partial=not name.startswith('fast-')))
    inputs={str(q.relative_to(s)):sha(q) for q in sorted((s/'inputs').rglob('*')) if q.is_file()}
    scripts={str(q.relative_to(ROOT)):sha(q) for q in sorted((ROOT/'experiments/late_write_isolation').glob('*.py'))}
    save(s/'manifest.json',dict(platform_commit=BASE,backend=dict(path=str(a.backend.resolve()),sha256=BACKEND),libraries=[dict(path=str(q),sha256=sha(q)) for q in libs],platform_sources=sources,input_hashes=inputs,script_hashes=scripts,conditions=cases,
         constraints=dict(per_command_seconds=60,rss_bytes=2**30,address_bytes=2**31,artifact_bytes=16*2**20,stage_monitored_seconds=300,stage_artifact_bytes=64*2**20,tmpfs_reserve_bytes=8*2**30)))
    print(json.dumps(dict(stage=str(s),conditions=len(cases),changed_link_id=changed[0]['id'])))
if __name__=='__main__':main()
