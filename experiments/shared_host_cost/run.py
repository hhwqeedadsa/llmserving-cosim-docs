"""Four preregistered timing observations; stop on any failure without reruns."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from experiments.shared_uplink_choice.prepare import load,save,sha


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();repo,output=a.repository.resolve(),a.output.resolve()
    output.mkdir();stage=output/'stage';stage.mkdir();(stage/'empty-cwd').mkdir();(output/'monitor').mkdir()
    env=os.environ|{'PYTHONPATH':str(repo/'python')+':'+str(repo),'PYTHONDONTWRITEBYTECODE':'1'}
    ledger=dict(state='started',closed=False,records=[],network_attempt_limit=4,network_reruns=0,
        scope='four new observations of previously accepted conditions, to acquire missing host phases')
    def script(module,*args):return [sys.executable,'-B','-m','experiments.shared_host_cost.'+module,*map(str,args)]
    def execute(label,argv,network=False):
        need=sum(r['network'] for r in ledger['records'])+int(network)
        assert not ledger['closed'] and need<=4
        seconds=sum(load(p).get('wall_seconds',0) for p in output.rglob('probe.json'))
        size=sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
        assert seconds+65<900 and size+66*2**20<768*2**20
        assert shutil.disk_usage(output).free>8*2**30
        if network:
            identity=load(stage/'runtime-identity.json')
            assert all(sha(stage/'runtime'/n)==d for n,d in identity['files'].items())
            assert all(sha(repo/n)==d for n,d in identity['sources'].items())
        row=dict(label=label,command=list(map(str,argv)),cwd=str(stage/'empty-cwd'),network=network,state='started')
        ledger['records'].append(row);save(stage/'execution.json',ledger)
        command=[sys.executable,'-B','-m','moe_ub_cosim.host_probe','--output-dir',str(output/'monitor'/label),
            '--rss-bytes',str(2**30),'--address-bytes',str(2**31),'--artifact-bytes',str(768*2**20),
            '--artifact-root',str(output),'--wall-seconds','60','--min-free-bytes',str(8*2**30),
            '--',*map(str,argv)]
        result=subprocess.run(command,cwd=stage/'empty-cwd',env=env,timeout=65)
        row.update(state='terminal',exit_code=result.returncode);save(stage/'execution.json',ledger)
        assert result.returncode==0,'preserve failure; close all remaining observations'
        print(label,'passed',flush=True)
    cli=[sys.executable,'-B','-m','moe_ub_cosim','scaleout']
    try:
        execute('prepare',script('prepare','--repository',repo,'--stage',stage))
        (stage/'truth').mkdir()
        for batch in (4,8):
            (stage/'truth'/f'batch{batch}').mkdir()
            for mode in ('modern','generic'):
                root=stage/'truth'/f'batch{batch}'/mode
                if mode=='modern':
                    argv=[*cli,'modern-deepep-native','--topology',stage/'observed/baseline-topology',
                        '--protocol',stage/'observed/baseline-protocol.json','--config',stage/'observed'/f'batch{batch}/input.json',
                        '--backend',stage/'runtime/run-transaction-session','--output',root,'--callback-batches','--host-timing']
                else:argv=script('generic','--stage',stage,'--batch',batch)
                execute(f'batch{batch}-{mode}',argv,True)
                execute(f'batch{batch}-{mode}-compare',script('analyze','compare','--stage',stage,'--batch',batch,'--mode',mode))
                execute(f'batch{batch}-{mode}-host-check',[*cli,'host-timing-check','--run',root,'--output',root/'host-summary.json'])
        ledger.update(state='completed',closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved_no_remaining_networks',closed=True,error=str(error));raise
    finally:save(stage/'execution.json',ledger)


if __name__=='__main__':main()
