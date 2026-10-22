"""Independent fixed-input study through public scaleout sweep, with one real pilot."""
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


def make_case(definition, design, target=None):
    rec=dict(initial_rto_ns=definition['initial_rto_ns'],max_rto_ns=64000 if definition['initial_rto_ns']==2000 else 160000,
             max_retries=8 if definition['initial_rto_ns']==2000 else 2 if definition['fault']=='persistent' else 3)
    if definition['history']:rec['history']=deepcopy(design['history'])
    admission={}
    if definition['shares']:admission['per_destination']=deepcopy(design['per_destination'])
    patch=dict(service=dict(workload=dict(batch_per_rank=definition['batch'])),protocol=dict(recovery=rec,admission=admission))
    if definition['fault']!='none':
        assert target is not None
        patch['protocol']['faults']=[dict(node=target['src'],port=0,message_id=target['id'],kind='ack',sequence=target['bytes'],
             count=1 if definition['fault']=='single' else 10000,start_ns=0,end_ns=5000000)]
    return dict(name=definition['name'],patches=patch,comparison_group='batch'+str(definition['batch']),
                expect='recovery_retry_exhausted' if definition['fault']=='persistent' else 'success')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True);p.add_argument('--protoc',required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(exist_ok=False);design=read(HERE/'design.json');commands=[];definitions=design['cases']
    assert len(definitions)==24 and len({c['name'] for c in definitions})==24
    pilot=definitions[0];assert pilot['fault']=='none'
    for name in ['service','protocol','topology']:shutil.copyfile(HERE/(name+'.json'),out/(name+'.json'))
    def sweep(name,cases,artifact_bytes):
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<256*1024**2
        spec=dict(schema_version='moe-scaleout-sweep/1',base={k:str(out/(k+'.json')) for k in ['service','protocol','topology']},
            budget=dict(wall_seconds=300,rss_bytes=1073741824,address_bytes=2147483648,artifact_bytes=artifact_bytes),cases=cases)
        save(out/(name+'.json'),spec)
        argv=[sys.executable,'-m','moe_ub_cosim','scaleout','sweep','--config',str(out/(name+'.json')),
              '--backend',str(a.backend),'--protoc',a.protoc,'--output',str(out/name)]
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            completed=subprocess.run(argv,cwd=ROOT,stdout=f,stderr=e,timeout=330)
        commands.append(dict(argv=argv,cwd=str(ROOT),exit_code=completed.returncode));save(out/'commands.json',commands)
        if completed.returncode:raise RuntimeError(name+': preserve failed attempt; no retry or budget increase')
        return read(out/name/'summary.json')
    first=sweep('pilot',[make_case(pilot,design)],64*1024**2)
    wave=read(out/'pilot'/pilot['name']/'run/graphs/g0.w0/wave.json')
    candidates=[m for m in wave['messages'] if all(m[k]==v for k,v in design['selector'].items())]
    assert len(candidates)==1,'Target must be selected from actual first graph without ambiguous matching'
    target=candidates[0];save(out/'target.json',target)
    rest=sweep('matrix',[make_case(c,design,target) for c in definitions[1:]],192*1024**2)
    results={c['name']:dict(metrics=c,run=str(Path('pilot' if c['name']==pilot['name'] else 'matrix')/c['name']/'run'))
             for c in [*first['cases'],*rest['cases']]}
    save(out/'experiment.json',dict(platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         design=design,target=target,cases=results,
         source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.iterdir() if p.suffix in ('.py','.json')},
         budget=dict(aggregate_artifact_bytes=256*1024**2,pilot_artifact_bytes=64*1024**2,matrix_artifact_bytes=192*1024**2,
                     sweep_wall_seconds=300,case_wall_seconds=60,rss_bytes=1073741824,address_bytes=2147483648),
         scope='24 finite synthetic EP4 inference conditions; actual source-selected fault; no hardware calibration or real hotspot evidence'))
    print('24 distinct inference conditions completed; persistent failures retained')


if __name__=='__main__':main()
