"""Execute the preregistered offline study once, with fixed failure/resource gates."""
import argparse
import os
from pathlib import Path
import sys
from moe_ub_cosim.host_probe import disk_usage, probe
from .study import load,save


def execute(repo,root,continue_offline=False):
    if continue_offline:
        original=load(root/'execution.json')
        assert original['closed'] and original['state']=='failure_preserved'
        assert len(original['records'])==1 and original['records'][0]['label']=='prepare' and original['records'][0]['exit_code']==1
        assert not (root/'offline-continuation.json').exists() and not (root/'analysis.json').exists()
    else:
        root.mkdir();(root/'empty-cwd').mkdir()
    record_path=root/('offline-continuation.json' if continue_offline else 'execution.json')
    ledger=dict(state='started',closed=False,new_networks=0,records=[])
    env=os.environ.copy();cwd=Path.cwd()
    os.environ.update(PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    try:
        os.chdir(root/'empty-cwd')
        for label,module,args in (
            ('prepare','study',('prepare','--repository',repo,'--run',root,*(['--continue-partial'] if continue_offline else []))),
            ('analyze','study',('analyze','--run',root,'--output',root/'analysis.json')),
            ('verify','verify',('--run',root,'--output',root/'verification.json')),
            ('report','report',('--run',root,'--output',root/'report')),
        ):
            seconds=sum(load(p)['wall_seconds'] for p in root.rglob('probe.json'))
            assert seconds+65<600 and max(disk_usage(root).values())+2*2**20<384*2**20
            argv=[sys.executable,'-B','-m','experiments.hybrid_path_audit.'+module,*map(str,args)]
            monitor_label=label+('-continued' if continue_offline else '')
            result=probe(argv,root/'monitor'/monitor_label,rss_bytes=2**30,address_bytes=2**31,
                artifact_bytes=384*2**20,wall_seconds=60,artifact_roots=(root,),min_free_bytes=128*2**20)
            ledger['records'].append(dict(label=label,command=argv,cwd=str(Path.cwd()),
                status=result['status'],exit_code=result['child_exit_code']))
            save(record_path,ledger)
            assert result['status']=='completed' and result['child_exit_code']==0,ledger['records'][-1]
            print(label,'passed',flush=True)
        ledger.update(state='completed',closed=True)
    except BaseException as error:
        ledger.update(state='failure_preserved',closed=True,error=repr(error))
        raise
    finally:
        save(record_path,ledger);os.chdir(cwd)
        os.environ.clear();os.environ.update(env)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--continue-offline',action='store_true');a=p.parse_args()
    execute(a.repository.resolve(),a.output.resolve(),a.continue_offline)
