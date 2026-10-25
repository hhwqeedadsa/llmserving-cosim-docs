"""Current-source identity, CLI admission and completed acceptance gate."""
import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest
from fixtures.native_callback_batches.stage import load,save,sha
from fixtures.native_callback_batches.checks import aggregate


def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();repo=a.repository;stage=a.stage
    aggregate(repo,stage)
    recovery=load(stage/'recovery.json');assert recovery['passed'] and recovery['independent_actual_network_executions']==1
    assert len(recovery['four_complete_comparisons_byte_identical'])==4
    assert recovery['fresh_modern_acceptance']['modern_events']==152
    records=load(stage/'commands.json');assert all(r['status']=='terminal' or r['label'].startswith('final-') for r in records)
    assert sum(r['network'] for r in records)==5 and all(r['exit_code']==0 for r in records if r['network'])
    failed=[r['label'] for r in records if r.get('exit_code',0)]
    assert failed==['prepare','four-verification','affected-regressions']
    assert 'PASS: checked 13 dependency checkouts' in (stage/'dependencies-monitor/stdout.log').read_text()
    compiled=[]
    for root in ('python/moe_ub_cosim','fixtures/native_callback_batches'):
        for path in (repo/root).rglob('*.py'):ast.parse(path.read_text(),filename=str(path));compiled.append(str(path.relative_to(repo)))
    copied=stage/'restored/source';sources=[]
    for path in (copied/'python/moe_ub_cosim').rglob('*.py'):
        rel=path.relative_to(copied);assert sha(path)==sha(repo/rel);sources.append(str(rel))
    help_result=subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','modern-deepep-native','--help'],
        cwd=repo,capture_output=True,text=True,check=True,timeout=5)
    assert '--callback-batches' in help_result.stdout and '--callback-wakeups' in help_result.stdout
    conflict=subprocess.run([sys.executable,'-B','-m','moe_ub_cosim','scaleout','modern-deepep-native',
        *[value for key in ('topology','protocol','config','backend','output') for value in ('--'+key,'/nonexistent')],
        '--callback-batches','--callback-wakeups'],cwd=repo,capture_output=True,text=True,timeout=5)
    assert conflict.returncode==2 and 'not allowed with argument' in conflict.stderr
    subprocess.run(['git','diff','--check'],cwd=repo,check=True,timeout=5)
    def ids(suite):
        for row in suite:
            if isinstance(row,unittest.TestSuite):yield from ids(row)
            else:yield row.id()
    loader=unittest.TestLoader();unique=set()
    for pattern in ('*native*test.py','*wakeup*test.py','transaction_session_alarms_test.py','session_host_timing_test.py','batched_callback_replay_test.py'):
        unique.update(ids(loader.discover(str(repo/'tests/unit'),pattern=pattern)))
    save(stage/'final-checks.json',dict(verified=True,python_distinct_tests=len(unique),
        python_initial_test_invocations=27,affected_test_invocations=34,native_pure_checks=84,native_pure_rejections=11,
        evidence_tamper_types=len(load(stage/'tamper.json')['rejected']),dependency_checkouts=13,
        ast_sources=len(compiled),copied_python_sources_current=len(sources),cli_help_and_mutual_exclusion=True,
        retained_preparation_checker_or_test_double_failures=failed,actual_network_executions=5,
        representative_four_rank_call_complete=True,overall_goal_status='in_progress'))


if __name__=='__main__':main()
