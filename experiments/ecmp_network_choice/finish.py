"""Close the retained partial study offline within its original fixed budget."""
import argparse
import os
from pathlib import Path
import shutil
import sys

from moe_ub_cosim.host_probe import disk_usage, probe
from .data import load, save, sha


def execute(repo, run):
    stage = run/'stage'
    ledger_digest = sha(stage/'execution.json')
    ledger = load(stage/'execution.json')
    assert ledger['closed'] and ledger['state'] == 'failure_preserved_no_remaining_networks'
    assert load(stage/'verification-final.json')['passed']
    assert not (run/'offline-closeout.json').exists()
    records = []
    env, original_cwd = os.environ.copy(), Path.cwd()
    os.environ.update(PYTHONPATH=str(repo/'python')+':'+str(repo), PYTHONDONTWRITEBYTECODE='1')

    def budget():
        # Conservatively include the original root monitor and copied historical receipts.
        seconds = sum(load(p)['wall_seconds'] for p in run.rglob('probe.json'))
        usage = disk_usage(run)
        assert seconds+65 < 1800 and max(usage.values())+2*2**20 < 2**30
        return seconds, usage

    def command(label, module, *args):
        budget()
        result = probe([sys.executable, '-B', '-m', module, *map(str, args)], run/'monitor'/label,
            rss_bytes=2**30, address_bytes=2**31, artifact_bytes=2**30, wall_seconds=60,
            artifact_roots=(run,), min_free_bytes=128*2**20)
        records.append(dict(label=label, status=result['status'], exit_code=result['child_exit_code']))
        save(run/'finish-execution.json', records)
        assert result['status'] == 'completed' and result['child_exit_code'] == 0, records[-1]
        print(label, 'passed', flush=True)

    try:
        os.chdir(stage/'empty-cwd')
        (stage/'report').rename(stage/'report-initial')
        command('report-final', 'experiments.ecmp_network_choice.evaluate', 'report',
                '--stage', stage, '--output', stage/'report')
        command('dependencies', 'moe_ub_cosim.host_probe',
                '--output-dir', run/'dependency-check', '--rss-bytes', 2**30,
                '--address-bytes', 2**31, '--artifact-bytes', 2**30,
                '--artifact-root', run, '--wall-seconds', 60, '--min-free-bytes', 128*2**20,
                '--', 'bash', repo/'scripts/check_deps.sh')
        archive = run/'checkpoint.tar.gz'
        command('checkpoint', 'experiments.ecmp_network_choice.checkpoint', 'create',
                '--repository', repo, '--run', run, '--archive', archive)
        digest = load(archive.with_suffix('.receipt.json'))['sha256']
        command('recovery', 'experiments.ecmp_network_choice.checkpoint', 'restore',
                '--archive', archive, '--sha256', digest, '--output', run/'recovered')
        assert load(run/'recovered/recovery.json')['passed']
        assert sha(stage/'execution.json') == ledger_digest
        output = repo/'experiments/ecmp_network_choice/results/20261027'
        output.mkdir(parents=True)
        for src, dst in (
            (stage/'report/summary.json', 'summary.json'),
            (stage/'report/report.md', 'report.md'), (stage/'report/report.html', 'report.html'),
            (stage/'diagnosis.json', 'diagnosis.json'), (stage/'verification-final.json', 'verification.json'),
            (run/'recovered/recovery.json', 'recovery.json'),
            (archive.with_suffix('.receipt.json'), 'checkpoint.json'),
        ):
            shutil.copyfile(src, output/dst)
        seconds, usage = budget()
        closeout = dict(passed=True, phase='completed_partial_with_preserved_strict_failure',
            original_execution_sha256=ledger_digest, original_execution_unchanged=True,
            executed_networks=6, unexecuted_networks=6, successful_network_reruns=0,
            strict_comparisons_passed=2, strict_comparisons_failed=1,
            original_strict_failure_preserved=True, records=records,
            conservative_monitored_seconds=seconds, artifact_usage=usage,
            receipt_reserve_bytes=2*2**20, management_reserve_seconds=10,
            time_budget_seconds=1800, artifact_budget_bytes=2**30,
            offline_closeout_networks=0, recovery_networks=0,
            builds=0, downloads=0, gpu_runs=0, dependency_changes=0, pushes=0,
            research_innovations_established=False, overall_research_complete=False)
        save(run/'offline-closeout.json', closeout)
        save(output/'closeout.json', closeout)
    finally:
        os.chdir(original_cwd)
        os.environ.clear()
        os.environ.update(env)
        save(run/'finish-execution.json', records)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--run', type=Path, required=True)
    a = p.parse_args()
    execute(a.repository.resolve(), a.run.resolve())
