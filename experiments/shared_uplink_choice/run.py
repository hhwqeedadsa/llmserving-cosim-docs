"""Public commands, explicit cumulative resources, and a failure-stop ledger."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from experiments.shared_uplink_choice.prepare import CONDITIONS, load, save, sha


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-failed-preparation',type=Path,
                   help='Only the retained zero-network preparation failure; never reopen its ledger')
    a=p.parse_args()
    repo,output=a.repository.resolve(),a.output.resolve()
    output.mkdir()
    stage,record=output/'stage',output/'monitor'
    stage.mkdir();record.mkdir();(stage/'empty-cwd').mkdir()
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    ledger=dict(state='started',closed=False,records=[],network_attempt_limit=12,reruns=0)
    prior=a.reuse_failed_preparation.resolve() if a.reuse_failed_preparation else None
    budget_roots=[output,*([prior] if prior else [])]
    def runtime_check():
        identity=load(stage/'runtime-identity.json')
        assert all(sha(stage/'runtime'/n)==d for n,d in identity['files'].items())
        assert all(sha(repo/n)==d for n,d in identity['sources'].items())
    def run(label,argv,network=False):
        assert not ledger['closed'] and sum(r['network'] for r in ledger['records'])+int(network)<=12
        seconds=sum(load(path).get('wall_seconds',0) for root in budget_roots for path in root.rglob('probe.json'))
        size=sum(path.stat().st_size for root in budget_roots for path in root.rglob('*') if path.is_file())
        assert seconds+65<1200 and size+34*2**20<2**30
        assert shutil.disk_usage(output).free>128*2**20
        if network:runtime_check()
        row=dict(label=label,command=list(map(str,argv)),cwd=str(stage/'empty-cwd'),network=network,state='started')
        ledger['records'].append(row)
        save(stage/'execution.json',ledger)
        monitor=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(record/(label+'-monitor')),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(2**30),
            *[arg for root in budget_roots for arg in ('--artifact-root',str(root))],
            '--wall-seconds','60','--min-free-bytes',str(128*2**20),
            '--',*map(str,argv)]
        result=subprocess.run(monitor,cwd=stage/'empty-cwd',env=env,timeout=65)
        row.update(state='terminal',exit_code=result.returncode)
        save(stage/'execution.json',ledger)
        assert result.returncode==0,'preserve failure and close remaining networks'
        print(label,'passed',flush=True)
    def script(module,*args):return [sys.executable,'-B','-m','experiments.shared_uplink_choice.'+module,*args]
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    try:
        if prior:
            previous=load(prior/'stage/execution.json')
            assert previous['closed'] and previous['state']=='failure_preserved_no_remaining_networks'
            assert sum(r['network'] for r in previous['records'])==0
            assert previous['records'][-1]['label']=='predict' and previous['records'][-1]['exit_code']==1
            assert all(r['exit_code']==0 for r in previous['records'][:-1])
            assert not (prior/'stage/truth').exists()
            for name in ('observed','runtime'):shutil.copytree(prior/'stage'/name,stage/name)
            shutil.copyfile(prior/'stage/runtime-identity.json',stage/'runtime-identity.json')
            runtime_check()
            save(stage/'preparation-reuse.json',dict(parent=str(prior),parent_execution_sha256=sha(prior/'stage/execution.json'),
                files={str(path.relative_to(stage)):sha(path) for name in ('observed','runtime')
                       for path in (stage/name).rglob('*') if path.is_file()},
                original_ledger_unchanged=True,original_networks=0,successful_preparation_commands_repeated=0,
                reason='Exclude token-specific slot_variable from equality of group binding parameters',
                prediction_rules_and_budgets_unchanged=True))
        else:
            run('prepare',script('prepare','--repository',repo,'--stage',stage))
            for batch in (4,8):
                root=stage/'observed'/f'batch{batch}'
                run(f'batch{batch}-plan',[*cli,'modern-deepep-plan','--config',root/'modern-input.json','--output',root/'plan'])
            for name in CONDITIONS:
                run(name+'-topology',[*cli,'topology','--config',stage/'observed'/(name+'-topology-config.json'),
                    '--output',stage/'observed'/(name+'-topology')])
        run('predict',script('predict','--observed',stage/'observed','--output',stage/'predictions'))
        if prior:assert (stage/'predictions/batch4.json').read_bytes()==(prior/'stage/predictions/batch4.json').read_bytes()
        frozen={str(path.relative_to(stage)):sha(path) for path in (stage/'predictions').iterdir()}
        save(stage/'prediction-identity.json',frozen)
        (stage/'truth').mkdir()
        for batch in (4,8):
            root=stage/'truth'/f'batch{batch}'
            root.mkdir()
            for name in CONDITIONS:
                assert all(sha(stage/n)==d for n,d in frozen.items())
                condition=root/name
                condition.mkdir()
                run(f'batch{batch}-{name}-modern',[*cli,'modern-deepep-native',
                    '--topology',stage/'observed'/(name+'-topology'),'--protocol',stage/'observed'/(name+'-protocol.json'),
                    '--config',stage/'observed'/f'batch{batch}/input.json','--backend',stage/'runtime/run-transaction-session',
                    '--output',condition/'modern','--callback-batches'],True)
                from experiments.compact_network_choice.evaluate import modern_metrics
                modern_metrics(condition/'modern')
                if name=='baseline':run(f'batch{batch}-compile',script('generic','compile','--stage',stage,'--batch',batch))
                run(f'batch{batch}-{name}-generic',script('generic','run','--stage',stage,'--batch',batch,'--name',name),True)
                run(f'batch{batch}-{name}-compare',script('evaluate','compare','--stage',stage,'--batch',batch,'--name',name))
        runtime_check()
        ledger.update(closed=True,state='completed')
    except BaseException as error:
        ledger.update(closed=True,state='failure_preserved_no_remaining_networks',error=str(error))
        raise
    finally:save(stage/'execution.json',ledger)


if __name__=='__main__':main()
