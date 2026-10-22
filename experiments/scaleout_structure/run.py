"""Fixed MoE input and separate incast transport validation via public commands."""
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
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);commands=[];backend=read(a.backend_json)
    def cli(label,argv,success=True):
        if shutil.disk_usage(out).free<8*1024**3 or sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>256*1024**2:
            raise RuntimeError('Experiment cumulative artifact/free-space budget exceeded')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,argv)]
        with (out/(label+'.stdout')).open('w') as stdout,(out/(label+'.stderr')).open('w') as stderr:
            r=subprocess.run(cmd,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=90)
        commands.append(dict(label=label,cwd=str(ROOT),argv=cmd,exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        if (r.returncode==0)!=success:raise RuntimeError(label+': unexpected exit; no automatic retry')
    cli('topology',['topology','--config',ROOT/'examples/scaleout_rdma/small-clos-100g.json','--output',out/'topology'])
    service=read(ROOT/'examples/scaleout_rdma/service.json')
    service['parallel']['groups']=[[0,2,4,6],[1,3,5,7]]
    service['model']['layers']=1;service['workload']['decode_steps']=2
    save(out/'service.json',service)
    protocol=read(ROOT/'examples/scaleout_rdma_admission/bytes8192.json')
    definitions=[('pass','passthrough',0,0),('count3','fifo',3,0),('bytes-fifo','fifo',3,8192),
        ('bytes-rr','destination_round_robin',3,8192),('bytes-ack','fifo',3,8192)]
    cases=[];target=None
    for name,policy,limit,byte_limit in definitions:
        c=deepcopy(protocol);c['admission']=dict(policy=policy,max_active_per_source=limit,
            max_active_bytes_per_source=byte_limit,decision_ns=0)
        if name=='bytes-ack':c['faults']=[dict(node=target['src'],port=0,message_id=target['id'],
            kind='ack',sequence=target['bytes'],count=1,start_ns=0,end_ns=5000000)]
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--topology',out/'topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',backend['service'],'--protoc',a.protoc,'--output',out/name],name!='bytes-ack')
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        r=read(out/name/'result.json')
        if not r['lifecycle_capture']['complete']:raise RuntimeError('Incomplete ownership record')
        if target is None:
            wave=read(out/'pass/graphs/g0.w0/wave.json')
            target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['src']==0 and m['dst']==4)
            save(out/'target.json',target)
        common=[]
        for path in (out/name/'graphs').glob('*/wave.json'):
            if path.read_bytes()!=(out/'pass/graphs'/path.parent.name/'wave.json').read_bytes():raise RuntimeError('Common graph changed')
            common.append(path.parent.name)
        if name=='bytes-ack' and r['fault_injection']['drop_counts']!=[1]:raise RuntimeError('ACK fault did not hit once')
        cases.append(dict(name=name,kind='MoE_service',passed=r['passed'],common_graphs=sorted(common)))
    # Separate network execution, never used as evidence of real MoE hotspots.
    messages=[dict(id=src,src=src,dst=7,bytes=131072,plane=0,ready_ns=0) for src in range(7)]
    save(out/'incast-messages.json',messages)
    for name,capture in [('incast-full',True),('incast-summary',False)]:
        c=deepcopy(protocol);c['admission']=dict(policy='passthrough',max_active_per_source=0,decision_ns=0)
        if capture:c['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
        save(out/(name+'.json'),c)
        cli(name,['rdma-probe','--topology',out/'topology','--config',out/(name+'.json'),
            '--messages',out/'incast-messages.json','--backend',backend['probe'],'--output',out/name])
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        cases.append(dict(name=name,kind='synthetic_transport_incast',passed=True))
    sources=['backend/rdma/fabric.cc','backend/communication_admission.hh','backend/queue_integral.hh',
        'python/moe_ub_cosim/scaleout_metrics.py','python/moe_ub_cosim/scaleout_admission.py',
        'experiments/scaleout_structure/run.py']
    save(out/'experiment.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},cases=cases,target=target))
    print('Five fixed MoE conditions and two separate incast observation conditions completed')


if __name__=='__main__':main()
