"""Bounded phases using public logical CLI only; no packet/GPU execution."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .study import load, save, sha, SYNTHETIC, POLICIES
from moe_ub_cosim.host_probe import disk_usage


def budget(run, reserve=65):
    receipts=[load(p) for p in (run/'monitor').glob('*/probe.json') if load(p)['status'] in ('completed','child_failed')]
    assert sum(p['wall_seconds'] for p in receipts)+reserve+10<600
    assert max(disk_usage(run).values())+2*2**20<256*2**20
    assert shutil.disk_usage(run).free>=128*2**20


def phase(repo,run,name):
    budget(run)
    ledger=load(run/'execution.json') if (run/'execution.json').exists() else dict(state='started',closed=False,records=[])
    assert not ledger['closed']
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    def command(label,argv):
        budget(run);row=dict(label=label,command=list(map(str,argv)),cwd=str(run/'empty-cwd'),exit_code=None)
        ledger['records'].append(row);save(run/'execution.json',ledger)
        logs=run/'logs';logs.mkdir(exist_ok=True)
        with (logs/(label+'.stdout')).open('wb') as stdout,(logs/(label+'.stderr')).open('wb') as stderr:
            before=time.monotonic();result=subprocess.run(row['command'],cwd=run/'empty-cwd',env=env,stdout=stdout,stderr=stderr,timeout=55)
        row.update(exit_code=result.returncode,wall_seconds=time.monotonic()-before);save(run/'execution.json',ledger)
        assert result.returncode==0,label
        print(label,'passed',flush=True)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    module=[sys.executable,'-B','-m','experiments.hybrid_causality_audit.study']
    try:
        if name=='prepare':
            (run/'empty-cwd').mkdir()
            command('prepare',[*module,'prepare','--repository',repo,'--run',run])
            save(run/'implementation-identity.json',{str(p.relative_to(repo)):sha(p) for directory in
                ('experiments/hybrid_causality_audit','python/moe_ub_cosim') for p in sorted((repo/directory).rglob('*.py'))})
        elif name=='plans':
            for case in load(run/'observed/cases.json'):
                command(case['name']+'-plan',[*cli,'hybrid-deepep-plan','--config',run/'observed/inputs'/(case['name']+'.json'),
                    '--output',run/'observed/plans'/case['name']])
        elif name=='environments':command('environments',[*module,'environments','--run',run])
        elif name=='predict':command('predict',[*module,'predict','--run',run,'--output',run/'analysis/predictions.json'])
        elif name.startswith('exhaustive-'):
            domains=int(name[-1]);command(name,[*module,'exhaustive','--run',run,'--domains',domains])
            assert load(run/'analysis'/(name+'.json'))['status']=='equivalent'
        else:
            assert name=='replays'
            for case in load(run/'observed/cases.json'):
                for policy in POLICIES:
                    command(case['name']+'-'+policy,[*cli,'hybrid-deepep-replay',
                        '--plan',run/'observed/plans'/case['name'],
                        '--actions',run/'observed/environments'/case['name']/(policy+'-actions.json'),
                        '--output',run/'truth'/case['name']/(policy+'.json')])
        ledger['last_completed_phase']=name
    except BaseException as error:
        ledger.update(state='failure_preserved',closed=True,error=repr(error));raise
    finally:save(run/'execution.json',ledger)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('prepare','plans','environments','predict','exhaustive-2','exhaustive-3','exhaustive-4','replays'))
    p.add_argument('--repository',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    phase(a.repository.resolve(),a.run.resolve(),a.phase)
