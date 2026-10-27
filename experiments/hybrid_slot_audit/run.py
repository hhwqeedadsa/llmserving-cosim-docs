"""Single bounded phase driver; public plans, separate observed/reference/truth."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .common import load,save,sha
from moe_ub_cosim.host_probe import disk_usage


def budget(run):
    receipts=[load(p) for p in (run/'monitor').glob('*/probe.json')]
    assert sum(r['wall_seconds'] for r in receipts)+75<600
    assert max(disk_usage(run).values())+2*2**20<256*2**20
    assert shutil.disk_usage(run).free>=128*2**20


def phase(repo,run,name):
    budget(run);path=run/'execution.json'
    ledger=load(path) if path.exists() else dict(state='started',closed=False,records=[])
    assert not ledger['closed']
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    def command(label,argv):
        budget(run);row=dict(label=label,command=list(map(str,argv)),cwd=str(run/'empty-cwd'),exit_code=None)
        ledger['records'].append(row);save(path,ledger)
        logs=run/'logs';logs.mkdir(exist_ok=True)
        with (logs/(label+'.stdout')).open('wb') as stdout,(logs/(label+'.stderr')).open('wb') as stderr:
            before=time.monotonic();value=subprocess.run(row['command'],cwd=run/'empty-cwd',env=env,stdout=stdout,stderr=stderr,timeout=55)
        row.update(exit_code=value.returncode,wall_seconds=time.monotonic()-before);save(path,ledger)
        assert value.returncode==0,label
        print(label,'passed',flush=True)
    module=[sys.executable,'-B','-m','experiments.hybrid_slot_audit.study']
    try:
        if name=='prepare':
            (run/'empty-cwd').mkdir()
            command('prepare',[*module,'prepare','--repository',repo,'--run',run])
            save(run/'initial-source-identity.json',{str(p.relative_to(repo)):sha(p) for p in (repo/'experiments/hybrid_slot_audit').glob('*.py')})
        elif name=='plans':
            for case in load(run/'observed/cases.json'):
                command(case['name']+'-plan',[sys.executable,'-B','-m','moe_ub_cosim','scaleout','hybrid-deepep-plan',
                    '--config',run/'observed/inputs'/(case['name']+'.json'),'--output',run/'observed/plans'/case['name']])
        elif name=='observe':command(name,[*module,name,'--run',run])
        else:
            assert name in ('reference','interpret')
            target=run/'analysis/reference.json' if name=='reference' else run/'truth'
            command(name,[*module,name,'--run',run,'--output',target])
        ledger['last_completed_phase']=name
    except BaseException as error:
        ledger.update(state='failure_preserved',closed=True,error=repr(error));raise
    finally:save(path,ledger)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('phase',choices=('prepare','plans','observe','reference','interpret'))
    p.add_argument('--repository',type=Path,required=True);p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    phase(a.repository.resolve(),a.run.resolve(),a.phase)
