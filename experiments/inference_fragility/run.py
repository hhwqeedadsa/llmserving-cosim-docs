"""Fixed-input inference fragility: adaptive recovery, silence and cold start."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);b=read(a.backend_json);commands=[]
    def cli(name,args,success=True):
        if shutil.disk_usage(out).free<8*1024**3 or sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>=256*1024**2:
            raise RuntimeError('Fixed experiment disk budget exceeded; no automatic retry')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            r=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=e,timeout=90)
        commands.append(dict(name=name,argv=cmd,cwd=str(ROOT),exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        if (r.returncode==0)!=success:raise RuntimeError(name+': unexpected exit; keep outputs and inspect')
    cli('topology',['topology','--config',HERE/'topology.json','--output',out/'topology'])
    shutil.copyfile(HERE/'service.json',out/'service.json');base=read(HERE/'protocol.json');target=None;cases=[]
    recovery=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=3,max_events=100000)
    for scenario in ['clean','loss','persistent','short']:
        for policy in ['fixed','adaptive']:
            name=policy+'-'+scenario;c=deepcopy(base);c['recovery']=dict(recovery)
            if policy=='adaptive':c['recovery']['rtt_estimator']=dict(kind='karn',min_rto_ns=1000,granularity_ns=1)
            if scenario=='short':c['recovery'].update(initial_rto_ns=2000,max_rto_ns=64000,max_retries=8)
            if scenario=='persistent':c['recovery']['max_retries']=2
            if scenario in ('loss','persistent'):
                c['faults']=[dict(node=target['src'],port=0,message_id=target['id'],kind='ack',sequence=target['bytes'],
                    count=1 if scenario=='loss' else 10000,start_ns=0,end_ns=5000000)]
            save(out/(name+'.json'),c);success=scenario!='persistent'
            cli(name,['rdma-run','--topology',out/'topology','--config',out/'service.json','--protocol',out/(name+'.json'),
                '--backend',b['service'],'--protoc',a.protoc,'--output',out/name],success)
            cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
            r=read(out/name/'result.json')
            if target is None:
                wave=read(out/name/'graphs/g0.w0/wave.json')
                target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['src']==0 and m['dst']==4)
                save(out/'target.json',target)
            graphs={}
            for path in sorted((out/name/'graphs').glob('*/wave.json')):
                assert path.read_bytes()==(out/'fixed-clean/graphs'/path.parent.name/'wave.json').read_bytes(),'Common graph changed'
                graphs[path.parent.name]=hashlib.sha256(path.read_bytes()).hexdigest()
            assert r['lifecycle_capture']['complete'] and r['recovery']['complete'],'Required evidence capture truncated'
            if scenario=='loss':assert r['fault_injection']['drop_counts']==[1]
            if scenario=='persistent':assert r['failure']['reason']=='recovery_retry_exhausted'
            cases.append(dict(name=name,passed=r['passed'],graphs=graphs))
    sources=[*[str(p.relative_to(ROOT)) for p in HERE.iterdir() if p.is_file() and p.suffix in ('.py','.json')],
        'backend/source_recovery.hh','backend/ack_rtt_estimator.hh','backend/rdma/recovery.cc',
        'python/moe_ub_cosim/scaleout_reliability.py']
    save(out/'experiment.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(sources)},target=target,cases=cases,
        scope='eight synthetic inference conditions; declared compute, initial RTO and floor; source-only online protocol'))
    print('Eight inference fragility conditions completed; persistent-loss failures retained')

if __name__=='__main__':main()
