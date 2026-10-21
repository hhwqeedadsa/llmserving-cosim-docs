"""Fixed offered workload; software concurrency/order/cost and packet-fault interventions."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True)
    p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(exist_ok=False)
    backend=read(a.backend_json);commands=[]
    def cli(label,argv,success=True):
        if sum(f.stat().st_size for f in out.rglob('*') if f.is_file())>128*1024**2 or shutil.disk_usage(out).free<8*1024**3:
            raise RuntimeError('Experiment cumulative artifact/free-space budget exceeded')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(label+'.stdout')).open('w') as stdout,(out/(label+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(label=label,argv=cmd,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        if (r.returncode==0)!=success:raise RuntimeError(label+': unexpected exit; inspect logs, no automatic retry')
    config=read(ROOT/'examples/scaleout_rdma/service.json')
    config['parallel']['groups']=[[0,2,4,6],[1,3,5,7]]
    config['model']['layers']=1;config['workload']['decode_steps']=2
    save(out/'service.json',config)
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    protocol=read(ROOT/'examples/scaleout_rdma/rdma.json')
    protocol['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
    protocol['lifecycle_capture']=dict(max_events=100000)
    definitions=[('pass','passthrough',0,0,None),('fifo1','fifo',1,0,None),('fifo2','fifo',2,0,None),
        ('rr1','destination_round_robin',1,0,None),('fifo1-cost10','fifo',1,10,None),
        ('fifo1-data','fifo',1,0,'data'),('fifo1-ack','fifo',1,0,'ack')]
    target=None;case_metadata=[]
    for label,policy,limit,cost,fault in definitions:
        rdma=deepcopy(protocol)
        rdma['admission']=dict(policy=policy,max_active_per_source=limit,decision_ns=cost)
        if fault:
            rdma['faults']=[dict(node=target['dst'] if fault=='data' else target['src'],port=0,
                message_id=target['id'],kind=fault,sequence=1000 if fault=='data' else target['bytes'],
                start_ns=0,end_ns=5000000,count=1)]
        save(out/(label+'.json'),rdma)
        cli(label,['rdma-run','--config',out/'service.json','--topology',out/'topology',
            '--protocol',out/(label+'.json'),'--backend',backend['service'],'--protoc',a.protoc,'--output',out/label],fault!='ack')
        cli(label+'-diagnose',['rdma-diagnose','--run',out/label,'--output',out/(label+'-diagnosis.json')])
        result=read(out/label/'result.json')
        if not result['packet_capture']['complete'] or not result['lifecycle_capture']['complete']:
            raise RuntimeError('Incomplete evaluation record; do not infer absent events')
        if target is None:
            wave=read(out/'pass/graphs/g0.w0/wave.json')
            target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['src']==0 and m['dst']==4)
            save(out/'selected-message.json',target)
        if fault and result['fault_injection']['drop_counts']!=[1]:raise RuntimeError('Fault did not hit exactly once')
        common=[]
        for path in (out/label/'graphs').glob('*/wave.json'):
            reference=out/'pass/graphs'/path.parent.name/'wave.json'
            if reference.read_bytes()!=path.read_bytes():raise RuntimeError('Common actual task graph changed')
            common.append(path.parent.name)
        case_metadata.append(dict(label=label,policy=policy,software_limit=limit,decision_ns=cost,fault=fault,
            passed=result['passed'],common_graphs=sorted(common)))
    sources=['backend/rdma/fabric.cc','backend/communication_admission.hh',
        'python/moe_ub_cosim/scaleout_admission.py','experiments/scaleout_admission/run.py']
    save(out/'experiment.json',dict(scope='fixed 8-GPU/two-EP4, one-layer, two-step declared-cost ready-KV Decode',
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
        cases=case_metadata,target=target))
    print('Seven fixed-workload interventions completed, including expected ACK stall')

if __name__=='__main__':main()
