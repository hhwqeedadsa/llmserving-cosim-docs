"""Five fixed MoE reliability conditions, executed through public CLI only."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend-json',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);backend=read(a.backend_json);commands=[]
    def cli(label,args,success=True):
        if shutil.disk_usage(out).free<8*1024**3 or sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>256*1024**2:
            raise RuntimeError('Experiment cumulative artifact/free-space budget exceeded')
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,args)]
        with (out/(label+'.stdout')).open('w') as f,(out/(label+'.stderr')).open('w') as e:
            r=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=e,timeout=90)
        commands.append(dict(label=label,cwd=str(ROOT),argv=cmd,exit_code=r.returncode,expected_success=success))
        save(out/'commands.json',commands)
        if (r.returncode==0)!=success:raise RuntimeError(label+': unexpected exit; inspect logs, no automatic retry')
    cli('topology',['topology','--config',HERE/'topology.json','--output',out/'topology'])
    shutil.copyfile(HERE/'service.json',out/'service.json');base=read(HERE/'protocol.json')
    recovery=dict(mode='go_back_n',initial_rto_ns=40000,max_rto_ns=160000,max_retries=3,max_events=100000)
    definitions=[('native-clean',False,0,True),('recovery-clean',True,0,True),
        ('native-loss',False,1,False),('recovery-loss',True,1,True),('persistent-loss',True,10000,False)]
    cases=[];target=None
    for name,enabled,count,success in definitions:
        c=deepcopy(base)
        if enabled:c['recovery']=dict(recovery,max_retries=2) if name=='persistent-loss' else recovery
        if count:c['faults']=[dict(node=target['src'],port=0,message_id=target['id'],kind='ack',
            sequence=target['bytes'],count=count,start_ns=0,end_ns=5000000)]
        save(out/(name+'.json'),c)
        cli(name,['rdma-run','--topology',out/'topology','--protocol',out/(name+'.json'),
            '--config',out/'service.json','--backend',backend['service'],'--protoc',a.protoc,'--output',out/name],success)
        cli(name+'-diagnose',['rdma-diagnose','--run',out/name,'--output',out/(name+'-diagnosis.json')])
        r=read(out/name/'result.json')
        if target is None:
            wave=read(out/name/'graphs/g0.w0/wave.json')
            target=next(m for m in wave['messages'] if m['phase']=='dispatch' and m['src']==0 and m['dst']==4)
            save(out/'target.json',target)
        common={}
        for path in sorted((out/name/'graphs').glob('*/wave.json')):
            assert path.read_bytes()==(out/'native-clean/graphs'/path.parent.name/'wave.json').read_bytes(),'Common graph changed'
            common[path.parent.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        assert r['lifecycle_capture']['complete'],'Incomplete resource history'
        if count:assert r['fault_injection']['drop_counts'][0]>0,'Fault never hit'
        cases.append(dict(name=name,passed=r['passed'],common_graphs=common))
    sources=['backend/rdma/recovery.cc','backend/retransmission_timer.hh','python/moe_ub_cosim/scaleout_recovery.py',
        *[str(p.relative_to(ROOT)) for p in HERE.iterdir() if p.is_file() and p.suffix in ('.py','.json')]]
    save(out/'experiment.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        sources={s:hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sorted(sources)},cases=cases,target=target,
        scope='synthetic fixed-input MoE reliability experiment; declared compute and RTO; no hardware calibration'))
    print('Five fixed MoE recovery conditions completed; expected failures retained')

if __name__=='__main__':main()
