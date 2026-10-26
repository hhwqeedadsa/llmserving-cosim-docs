"""Reject falsified selective feedback, fault hits and application completion."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import unittest
from fixtures.modern_selective.verify import verify,load


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();root=a.stage/'small-fault';original=load(root/'result.json');rejected=[]
    changes={
        'fake_mode':lambda r:r['transactions']['transport']['recovery']['config'].update(mode='go_back_n/1'),
        'unfinished_prefix':lambda r:r['transactions']['transport']['recovery']['states'][0].update(prefix_complete=False),
        'missing_fault_hit':lambda r:r.update(fault_drops=0),
        'false_feedback_bytes':lambda r:next(e for e in r['transactions']['transport']['selective_repeat']['events']
            if e['kind']=='feedback_received').update(extension_hex='00'*16),
        'lost_receiver_bytes':lambda r:r['transactions']['transport']['selective_repeat']['states'][0].update(receiver_prefix=0),
        'duplicate_target_apply':lambda r:r['transactions']['target_events'].append(deepcopy(r['transactions']['target_events'][0])),
        'missing_callback':lambda r:r['session']['events'].pop(),
        'truncated_recovery':lambda r:r['transactions']['transport']['recovery'].update(omitted_events=1),
    }
    for name,change in changes.items():
        result=deepcopy(original);change(result)
        try:verify(root,{'result.json':result})
        except (AssertionError,KeyError,ValueError):rejected.append(name)
        else:raise AssertionError('falsified result accepted: '+name)
    suites=('session_host_timing_test','native_callback_batches_test',
            'scaleout_selective_repeat_test','scaleout_transaction_selective_test')
    suite=unittest.TestSuite(unittest.defaultTestLoader.discover('tests/unit',pattern=name+'.py') for name in suites)
    result=unittest.TextTestRunner(verbosity=1).run(suite);assert result.wasSuccessful()
    reports={name:verify(a.stage/name) for name in ('small-healthy','small-fault','four-healthy','four-fault')}
    for name,report in reports.items():(a.stage/(name+'-verification.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (a.stage/'verification.json').write_text(json.dumps(dict(passed=True,cases=reports,
        tamper_rejections=rejected,affected_regression_tests=result.testsRun,additional_network_executions=0),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
