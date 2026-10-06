"""Run the standalone native example through public commands in a new directory."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

from moe_ub_cosim.research_storage import stored_rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[2];out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    example=out/'example';shutil.copytree(root/'examples/native_first_round',example)
    read=lambda path:json.loads(path.read_text())
    def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
    commands=[]
    def cli(label,*args,fail=False):
        argv=[sys.executable,'-m','moe_ub_cosim',*map(str,args)];started=datetime.now(timezone.utc).isoformat()
        r=subprocess.run(argv,cwd=root,capture_output=True,text=True,timeout=360)
        for s in ('stdout','stderr'):(out/(label+'.'+s+'.log')).write_text(getattr(r,s))
        commands.append(dict(argv=argv,cwd=str(root),started_utc=started,exit_code=r.returncode));save(out/'commands.json',commands)
        assert (r.returncode!=0) if fail else r.returncode==0,(label,r.stderr)
        return r
    for label,config in [('input','scenario.yaml'),('slower-input','scenario-slower.yaml')]:
        cli('resolve-'+label,'resolve','--config',example/config,'--backend',a.backend.resolve(),'--output',out/label)
    run=read(example/'run.json');run['inputs']={k:str(out/'input'/(k+'.json' if k!='network' else k)) for k in run['inputs']}
    save(example/'run.json',run)
    cli('run','run','--config',example/'run.json','--backend',a.backend.resolve(),'--output',out/'run')
    cli('validate','validate','--run',out/'run');cli('observed','validate','--observed',out/'run/observed')
    cli('freeze','slice','create','--from-run',out/'run','--output',out/'slice')
    cli('inspect','slice','inspect','--slice',out/'slice','--verify')
    cli('sample','sample','--slice',out/'slice','--config',example/'sample.json','--output',out/'sample')
    cli('sample-validate','validate','--run',out/'sample')
    cli('unsupported','sample','--slice',out/'slice','--config',example/'sample-unsupported.json','--output',out/'unsupported',fail=True)
    assert not (out/'unsupported').exists()
    for label in ('replay','replay-slower'):
        if label=='replay-slower':
            config=read(example/(label+'.json'));config['network']=str(out/'slower-input/network');save(example/(label+'.json'),config)
        cli(label,'replay','--slice',out/'slice','--config',example/(label+'.json'),'--backend',a.backend.resolve(),'--output',out/label)
        cli('validate-'+label,'validate','--run',out/label)
    for scope in ('observed','truth','metrics'):
        for name in read(out/'run'/scope/'storage.json')['fields']:
            assert list(stored_rows(out/'run'/scope,name))==list(stored_rows(out/'replay'/scope,name)),(scope,name)
    original=read(out/'run/manifest.json');slower=read(out/'replay-slower/manifest.json')
    assert slower['simulation_end_ns']>original['simulation_end_ns']
    save(out/'summary.json',dict(passed=True,commands=len(commands),simulation_end_ns=original['simulation_end_ns'],
        slower_simulation_end_ns=slower['simulation_end_ns'],same_replay_tables=True,unsupported_sampling_rejected_before_output=True,
        source='examples/native_first_round copied into a new directory; no historical run or external data'))
    print('PASS standalone native example: resolve/run/freeze/inspect/sample/replay/validate and directed service override')


if __name__=='__main__':main()
