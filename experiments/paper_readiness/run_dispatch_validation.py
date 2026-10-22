"""Run the fixed 18 conditions through public CLI; stop on failure, never expand."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('observed','backend','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();observed,backend,out=(p.resolve() for p in (args.observed,args.backend,args.output))
    assert not out.exists() and backend.is_file();index=json.loads((observed/'index.json').read_text());cfg=index['config']
    assert cfg['schema_version']=='moe-dispatch-validation/1'
    for name,info in index['artifacts'].items():assert sha(observed/name)==info['sha256']
    out.mkdir();commands=[]
    def cli(label,arguments):
        command=[sys.executable,'-m','moe_ub_cosim','scaleout',*map(str,arguments)]
        row=dict(label=label,command=command,cwd=str(ROOT));commands.append(row);start=time.monotonic()
        try:
            with (out/(label+'.stdout.log')).open('w') as stdout,(out/(label+'.stderr.log')).open('w') as stderr:
                child=subprocess.run(command,cwd=ROOT,stdout=stdout,stderr=stderr,timeout=60)
            row['exit_code']=child.returncode
            assert child.returncode==0,'failed CLI; preserve and stop'
        finally:row['wall_seconds']=time.monotonic()-start;save(out/'commands.json',commands)
    for network in cfg['network_profiles']:
        cli('topology-'+network,['topology','--config',observed/f'topology-{network}.json','--output',out/('topology-'+network)])
        for placement in cfg['placement_conditions']:
            label=network+'-'+placement
            cli(label,['rdma-stream-probe','--topology',out/('topology-'+network),'--protocol',observed/f'protocol-{network}.json',
                '--config',observed/f'work-{placement}.json','--backend',backend,'--output',out/label])
    assert len(commands)==24
    save(out/'execution.json',dict(completed=True,commands=len(commands),conditions=18,backend_sha256=sha(backend),
        observed_index_sha256=sha(observed/'index.json'),platform_base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        new_network_runs=18,modern_GIN_executed=False,hardware_calibrated=False,full_inference_modeled=False))
    print(json.dumps(dict(completed=True,commands=len(commands),conditions=18)))


if __name__=='__main__':main()
