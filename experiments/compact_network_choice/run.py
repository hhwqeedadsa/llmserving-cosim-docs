"""Budgeted public-CLI research workflow; failures stop all remaining networks."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

from experiments.compact_network_choice.prepare import CONDITIONS, load, save, sha


def main():
    p = argparse.ArgumentParser()
    for name in ('repository','stage','record'): p.add_argument('--'+name,type=Path,required=True)
    a = p.parse_args(); repo,stage,record = a.repository.resolve(),a.stage.resolve(),a.record.resolve()
    assert not stage.exists() and not record.exists()
    stage.mkdir();record.mkdir();(stage/'empty-cwd').mkdir()
    env = os.environ | {'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    ledger = dict(state='started',closed=False,records=[],network_attempt_limit=12,reruns=0)
    def runtime_check():
        identity = load(stage/'runtime-identity.json')
        assert all(sha(stage/'runtime'/n)==d for n,d in identity['files'].items())
        assert all(sha(repo/n)==d for n,d in identity['sources'].items())
    def run(label, argv, network=False):
        assert not ledger['closed'] and sum(r['network'] for r in ledger['records'])+int(network)<=12
        seconds = sum(load(p).get('wall_seconds',0) for root in (stage,record) for p in root.rglob('probe.json'))
        size = sum(p.stat().st_size for root in (stage,record) for p in root.rglob('*') if p.is_file())
        assert seconds+65<1200 and size+64*2**20+2*2**20<2**30
        assert shutil.disk_usage(stage).free>8*2**30 and shutil.disk_usage(record).free>64*2**20
        if network: runtime_check()
        row = dict(label=label,command=list(map(str,argv)),cwd=str(stage/'empty-cwd'),network=network,state='started')
        ledger['records'].append(row);save(stage/'execution.json',ledger)
        monitor = [sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(record/(label+'-monitor')),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(2**30),
            '--artifact-root',str(stage),'--artifact-root',str(record),'--wall-seconds','60',
            '--min-free-bytes',str(8*2**30),'--',*map(str,argv)]
        result = subprocess.run(monitor,cwd=stage/'empty-cwd',env=env,timeout=65)
        row.update(state='terminal',exit_code=result.returncode);save(stage/'execution.json',ledger)
        assert result.returncode==0,'preserve failure and stop; do not expand budgets or rerun'
        print(label,'passed',flush=True)
    def script(name,*args):return [sys.executable,'-B','-m','experiments.compact_network_choice.'+name,*args]
    cli = [sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    try:
        run('prepare',script('prepare','--repository',repo,'--stage',stage))
        for batch in (4,8):
            root = stage/'observed'/f'batch{batch}'
            run(f'batch{batch}-plan',[*cli,'modern-deepep-plan','--config',root/'modern-input.json','--output',root/'plan'])
        for name in CONDITIONS:
            run(name+'-topology',[*cli,'topology','--config',stage/'observed'/(name+'-topology-config.json'),
                                    '--output',stage/'observed'/(name+'-topology')])
        run('predict',script('compact','--observed',stage/'observed','--output',stage/'predictions'))
        frozen = {str(p.relative_to(stage)):sha(p) for p in (stage/'predictions').iterdir()}
        save(stage/'prediction-identity.json',frozen)
        (stage/'truth').mkdir()
        for batch in (4,8):
            root = stage/'truth'/f'batch{batch}';root.mkdir()
            for name in CONDITIONS:
                assert all(sha(stage/p)==digest for p,digest in frozen.items())
                condition = root/name;condition.mkdir()
                run(f'batch{batch}-{name}-modern',[*cli,'modern-deepep-native',
                    '--topology',stage/'observed'/(name+'-topology'),'--protocol',stage/'observed'/(name+'-protocol.json'),
                    '--config',stage/'observed'/f'batch{batch}'/'input.json','--backend',stage/'runtime/run-transaction-session',
                    '--output',condition/'modern','--callback-batches'],True)
                from experiments.compact_network_choice.evaluate import modern_metrics
                modern_metrics(condition/'modern')
                if name=='baseline':
                    run(f'batch{batch}-compile',script('generic','compile','--stage',stage,'--batch',str(batch)))
                run(f'batch{batch}-{name}-generic',script('generic','run','--stage',stage,'--batch',str(batch),'--name',name),True)
                run(f'batch{batch}-{name}-compare',script('evaluate','compare','--stage',stage,'--batch',str(batch),'--name',name))
        runtime_check();ledger.update(closed=True,state='completed')
    except BaseException as error:
        ledger.update(closed=True,state='failure_preserved_no_remaining_networks',error=str(error))
        raise
    finally:save(stage/'execution.json',ledger)


if __name__ == '__main__': main()
