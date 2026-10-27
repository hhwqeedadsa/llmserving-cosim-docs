"""Account and archive the original failure and completed offline continuation."""
import argparse
import os
from pathlib import Path
import shutil
import sys
from moe_ub_cosim.host_probe import disk_usage,probe
from .study import load,save,sha


def execute(repo,run):
    assert load(run/'verification.json')['passed'] and not (run/'closeout.json').exists()
    digest=sha(run/'execution.json');records=[];env=os.environ.copy();cwd=Path.cwd()
    os.environ.update(PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    def budget():
        seconds=sum(load(p)['wall_seconds'] for p in run.rglob('probe.json'));usage=disk_usage(run)
        assert seconds+65<600 and max(usage.values())+2*2**20<384*2**20
        return seconds,usage
    def command(label,argv):
        budget()
        result=probe(list(map(str,argv)),run/'monitor'/label,rss_bytes=2**30,address_bytes=2**31,
            artifact_bytes=384*2**20,wall_seconds=60,artifact_roots=(run,),min_free_bytes=128*2**20)
        records.append(dict(label=label,exit_code=result['child_exit_code'],status=result['status']))
        save(run/'finish-execution.json',records)
        assert result['status']=='completed' and result['child_exit_code']==0,records[-1]
        print(label,'passed',flush=True)
    try:
        os.chdir(run/'empty-cwd')
        command('dependencies',['bash',repo/'scripts/check_deps.sh'])
        archive=run/'checkpoint.tar.gz'
        command('checkpoint',[sys.executable,'-B','-m','experiments.hybrid_path_audit.checkpoint','create',
            '--repository',repo,'--run',run,'--archive',archive])
        archive_digest=load(archive.with_suffix('.receipt.json'))['sha256']
        command('recovery',[sys.executable,'-B','-m','experiments.hybrid_path_audit.checkpoint','restore',
            '--archive',archive,'--sha256',archive_digest,'--output',run/'recovered'])
        assert load(run/'recovered/recovery.json')['passed'] and sha(run/'execution.json')==digest
        out=repo/'experiments/hybrid_path_audit/results/20261027';out.mkdir(parents=True)
        for src,dst in ((run/'analysis.json','analysis.json'),(run/'verification.json','verification.json'),
            (run/'report/report.md','report.md'),(run/'report/report.html','report.html'),
            (run/'report/key_metrics.json','key_metrics.json'),(run/'recovered/recovery.json','recovery.json'),
            (archive.with_suffix('.receipt.json'),'checkpoint.json')):
            shutil.copyfile(src,out/dst)
        seconds,usage=budget()
        result=dict(passed=True,phase='completed_with_preserved_input_preparation_failure',
            original_execution_sha256=digest,original_execution_unchanged=True,records=records,
            conservative_monitored_seconds=seconds,artifact_usage=usage,
            time_budget_seconds=600,artifact_budget_bytes=384*2**20,
            management_reserve_seconds=10,receipt_reserve_bytes=2*2**20,
            network_runs=0,builds=0,downloads=0,GPU_runs=0,dependency_changes=0,pushes=0,
            research_innovations_established=False,overall_goal_complete=False)
        save(run/'closeout.json',result);save(out/'closeout.json',result)
    finally:
        os.chdir(cwd);os.environ.clear();os.environ.update(env);save(run/'finish-execution.json',records)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    execute(a.repository.resolve(),a.run.resolve())
