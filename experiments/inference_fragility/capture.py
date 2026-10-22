"""Two bounded packet-observation replays to explain the existing loss pair."""
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
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--experiment',type=Path,required=True);p.add_argument('--backend-json',type=Path,required=True)
    p.add_argument('--protoc',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=a.experiment.resolve();out=a.output.resolve();out.mkdir(exist_ok=False);backend=read(a.backend_json);commands=[]
    for name in ['fixed-loss','adaptive-loss']:
        assert shutil.disk_usage(out).free>=8*1024**3
        assert sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<128*1024**2
        c=read(source/(name+'.json'));c['packet_capture']=dict(nodes=[],start_ns=0,end_ns=5000000,max_events=100000)
        save(out/(name+'.json'),c)
        cmd=[sys.executable,'-m','moe_ub_cosim','scaleout','rdma-run','--topology',str(source/'topology'),
            '--config',str(source/'service.json'),'--protocol',str(out/(name+'.json')),
            '--backend',backend['service'],'--protoc',a.protoc,'--output',str(out/name)]
        with (out/(name+'.stdout')).open('w') as f,(out/(name+'.stderr')).open('w') as e:
            r=subprocess.run(cmd,cwd=ROOT,stdout=f,stderr=e,timeout=90)
        commands.append(dict(argv=cmd,cwd=str(ROOT),exit_code=r.returncode));save(out/'commands.json',commands)
        if r.returncode:raise RuntimeError(name+': packet observation failed; no retry')
        observed=read(out/name/'result.json');capture=observed.pop('packet_capture')
        assert capture['complete'] and observed==read(source/name/'result.json'),'Packet recording changed execution or truncated'
        for path in (out/name/'graphs').glob('*/wave.json'):
            assert path.read_bytes()==(source/name/'graphs'/path.parent.name/'wave.json').read_bytes()
    save(out/'capture.json',dict(passed=True,source_experiment=str(source),cases=['fixed-loss','adaptive-loss'],
        platform_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        capture_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        scope='passive packet capture; both full executions equal their uncaptured baselines'))
    print('Two packet captures match existing MoE executions exactly')

if __name__=='__main__':main()
