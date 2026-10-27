"""Preregistered public workflows, immutable predictions, stop on first failure."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from moe_ub_cosim.host_probe import disk_usage
from .data import CONDITIONS, load, save, sha, runtime_check


def execute(repo,root):
    root.mkdir();stage=root/'stage';stage.mkdir();(stage/'empty-cwd').mkdir()
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    ledger=dict(state='started',closed=False,records=[],network_attempt_limit=12,reruns=0)
    def command(label,argv,network=False):
        assert not ledger['closed'] and sum(r['network'] for r in ledger['records'])+int(network) <= 12
        seconds=sum(load(p)['wall_seconds'] for p in root.rglob('probe.json'))
        assert seconds+65 < 1800 and max(disk_usage(root).values())+64*2**20 < 2**30
        if network:
            runtime_check(stage,repo)
            assert all(sha(stage/n) == h for n,h in load(stage/'input-identity.json').items())
        row=dict(label=label,command=list(map(str,argv)),cwd=str(stage/'empty-cwd'),network=network,state='started')
        ledger['records'].append(row);save(stage/'execution.json',ledger)
        monitored=[sys.executable,'-B','-m','moe_ub_cosim.host_probe',
            '--output-dir',str(root/'monitor'/label),'--rss-bytes',str(2**30),'--address-bytes',str(2**31),
            '--artifact-bytes',str(2**30),'--artifact-root',str(root),'--wall-seconds','60',
            '--min-free-bytes',str(128*2**20),'--',*map(str,argv)]
        result=subprocess.run(monitored,cwd=stage/'empty-cwd',env=env,timeout=65)
        row.update(state='terminal',exit_code=result.returncode);save(stage/'execution.json',ledger)
        assert result.returncode == 0,'preserve failure and close remaining networks'
        print(label,'passed',flush=True)
    def script(module,*args):return [sys.executable,'-B','-m','experiments.ecmp_network_choice.'+module,*args]
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    try:
        command('prepare',script('data','--repository',repo,'--stage',stage))
        observed=stage/'observed'
        for name in CONDITIONS:
            command(name+'-topology',[*cli,'topology','--config',observed/(name+'-topology-config.json'),
                '--output',observed/(name+'-topology')])
        command('predict',script('predict','--observed',observed,'--output',stage/'predictions'))
        save(stage/'input-identity.json',{str(p.relative_to(stage)):sha(p)
            for directory in (observed,stage/'predictions') for p in directory.rglob('*') if p.is_file()})
        for batch in (4,8):
            base=stage/f'truth/batch{batch}';base.mkdir()
            for name in CONDITIONS:
                (base/name).mkdir()
                label=f'batch{batch}-{name}'
                command(label+'-modern',[*cli,'modern-deepep-native','--topology',observed/(name+'-topology'),
                    '--protocol',observed/(name+'-protocol.json'),'--config',observed/f'batch{batch}/input.json',
                    '--backend',stage/'runtime/run-transaction-session','--output',base/name/'modern','--callback-batches'],True)
                command(label+'-check',script('evaluate','check','--stage',stage,'--batch',batch,'--name',name))
                if name == 'single':
                    command(f'batch{batch}-compile',script('generic','compile','--stage',stage,'--batch',batch,'--name',name))
                command(label+'-generic',script('generic','run','--stage',stage,'--batch',batch,'--name',name),True)
                command(label+'-compare',script('evaluate','compare','--stage',stage,'--batch',batch,'--name',name))
        runtime_check(stage,repo)
        ledger.update(state='completed',closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_no_remaining_networks',closed=True,error=repr(error))
        raise
    finally:save(stage/'execution.json',ledger)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    execute(a.repository.resolve(),a.output.resolve())
