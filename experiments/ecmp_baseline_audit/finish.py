"""Finish offline checking/recovery in the same fixed budget; retain first failure."""
import argparse
from pathlib import Path
import os
import shutil
import sys

from moe_ub_cosim.host_probe import disk_usage, probe
from .model import load, save, sha


def execute(repo,run):
    ledger = load(run/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'failure_preserved'
    assert [r['label'] for r in ledger['records']] == ['prepare','cores1','cores2','cores4','freeze','predict','verify']
    assert all(r['exit_code'] == 0 for r in ledger['records'][:-1]) and ledger['records'][-1]['exit_code'] == 1
    assert load(run/'verification.json')['passed']
    assert not (run/'offline-closeout.json').exists()
    original = Path.cwd()
    records = []
    env = os.environ.copy()
    os.environ.update(PYTHONPATH=str(repo/'python')+':'+str(repo),PYTHONDONTWRITEBYTECODE='1')
    def command(label,module,*args):
        seconds = sum(load(p)['wall_seconds'] for p in (run/'monitor').glob('*/probe.json'))
        assert seconds+65 < 600 and max(disk_usage(run).values())+2*2**20 < 256*2**20
        result = probe([sys.executable,'-B','-m',module,*map(str,args)],run/'monitor'/label,
            rss_bytes=2**30,address_bytes=2**31,artifact_bytes=256*2**20,wall_seconds=60,
            artifact_roots=(run,),min_free_bytes=128*2**20)
        records.append(dict(label=label,status=result['status'],exit_code=result['child_exit_code']))
        assert result['status'] == 'completed' and result['child_exit_code'] == 0
        print(label,'passed',flush=True)
    try:
        os.chdir(run/'empty-cwd')
        (run/'report').rename(run/'report-initial')
        command('report-rendered','experiments.ecmp_baseline_audit.study','report',
            '--run',run,'--output',run/'report')
        archive = run/'checkpoint.tar.gz'
        command('checkpoint','experiments.ecmp_baseline_audit.checkpoint','create',
            '--repository',repo,'--run',run,'--archive',archive)
        digest = load(archive.with_suffix('.receipt.json'))['sha256']
        command('recovery','experiments.ecmp_baseline_audit.checkpoint','restore',
            '--archive',archive,'--sha256',digest,'--output',run/'recovered')
        assert load(run/'recovered/recovery.json')['passed']
        output = repo/'experiments/ecmp_baseline_audit/results/20261027'
        output.mkdir(parents=True)
        for src,dst in ((run/'predictions/summary.json','summary.json'),
                (run/'verification.json','verification.json'),(run/'recovered/recovery.json','recovery.json'),
                (run/'checkpoint.tar.receipt.json','checkpoint.json'),
                (run/'report/report.md','report.md'),(run/'report/report.html','report.html')):
            shutil.copyfile(src,output/dst)
        usage = disk_usage(run)
        seconds = sum(load(p)['wall_seconds'] for p in (run/'monitor').glob('*/probe.json'))
        assert seconds+10 < 600 and max(usage.values())+2*2**20 < 256*2**20
        closeout = dict(passed=True,phase='completed_with_preserved_checker_failure',
            original_execution_sha256=sha(run/'execution.json'),original_execution_unchanged=True,
            records=records,monitored_seconds=seconds,artifact_usage=usage,
            receipt_reserve_bytes=2*2**20,management_reserve_seconds=10,
            time_budget_seconds=600,artifact_budget_bytes=256*2**20,
            new_networks=0,topology_generations=3,successful_topology_or_prediction_reruns=0,
            builds=0,downloads=0,gpu_runs=0,pushes=0)
        save(run/'offline-closeout.json',closeout)
        save(output/'closeout.json',closeout)
    finally:
        os.chdir(original)
        os.environ.clear();os.environ.update(env)
        save(run/'finish-execution.json',dict(records=records))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--repository',type=Path,required=True)
    p.add_argument('--run',type=Path,required=True)
    a=p.parse_args();execute(a.repository.resolve(),a.run.resolve())
