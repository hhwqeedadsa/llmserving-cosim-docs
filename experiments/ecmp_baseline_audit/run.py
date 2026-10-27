"""Fixed three topology generations, no backend process; stop on first failure."""
import argparse
import os
from pathlib import Path
import sys

from moe_ub_cosim.host_probe import disk_usage, probe
from .model import load, save


def execute(repo, root):
    root.mkdir()
    (root/'empty-cwd').mkdir()
    environment = os.environ.copy()
    os.environ['PYTHONPATH'] = str(repo/'python')+':'+str(repo)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    ledger = dict(state='started',closed=False,new_networks=0,records=[])
    def command(label, module, *args):
        seconds = sum(load(p)['wall_seconds'] for p in (root/'monitor').glob('*/probe.json'))
        assert seconds+65 < 600 and max(disk_usage(root).values())+2*2**20 < 256*2**20
        row = dict(label=label,state='started')
        ledger['records'].append(row)
        save(root/'execution.json',ledger)
        argv = [sys.executable,'-B','-m',module,*map(str,args)]
        result = probe(argv,root/'monitor'/label,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=256*2**20,wall_seconds=60,artifact_roots=(root,),min_free_bytes=128*2**20)
        row.update(state='terminal',exit_code=result['child_exit_code'],status=result['status'])
        save(root/'execution.json',ledger)
        assert result['status'] == 'completed' and result['child_exit_code'] == 0
        print(label,'passed',flush=True)
    original = Path.cwd()
    try:
        os.chdir(root/'empty-cwd')
        command('prepare','experiments.ecmp_baseline_audit.study','prepare','--repository',repo,'--run',root)
        for cores in (1,2,4):
            command(f'cores{cores}','moe_ub_cosim','scaleout','topology',
                '--config',root/f'observed/cores{cores}-config.json','--output',root/f'observed/cores{cores}')
        command('freeze','experiments.ecmp_baseline_audit.study','freeze','--run',root)
        command('predict','experiments.ecmp_baseline_audit.model','--observed',root/'observed','--output',root/'predictions')
        command('verify','experiments.ecmp_baseline_audit.verify','--run',root,'--output',root/'verification.json')
        command('report','experiments.ecmp_baseline_audit.study','report','--run',root,'--output',root/'report')
        ledger.update(closed=True,state='completed')
    except BaseException as error:
        ledger.update(closed=True,state='failure_preserved',error=repr(error))
        raise
    finally:
        os.chdir(original)
        os.environ.clear()
        os.environ.update(environment)
        save(root/'execution.json',ledger)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a = p.parse_args()
    execute(a.repository.resolve(),a.output.resolve())
