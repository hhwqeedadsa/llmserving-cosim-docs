"""Offline acceptance evidence checks; no simulator, kernel or compiler call."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def check(rows, example):
    results={r['case']:r for r in rows}
    assert len(results)==5
    for name in ('healthy','lost_reply_replay'):
        r=results[name]
        assert (r['target_value'],r['returned_value'],r['completed'],r['atomic_copies'],r['credit'])==(46,41,1,1,1)
        assert r['reply_opcode']==18 and r['reply_psn']==1
    r=results['duplicate_write_after_lost_atomic_ack']
    assert (r['target_value'],r['returned_value'],r['completed'],r['atomic_copies'],r['credit'])==(46,0xdeadbeef,2,0,1)
    assert r['reply_opcode']==17 and r['reply_psn']==1
    r=results['later_psn_ordinary_ack']
    assert (r['completed'],r['atomic_copies'],r['credit'],r['state'])==(0,0,0,10)
    r=results['evicted_atomic_replay']
    assert r['target_value']==53 and r['reply_opcode']==0
    assert not example['network_executed'] and not example['hardware_calibrated'] and not example['exact_RXE_equivalence']
    for key in ('healthy','lost_feedback'):
        e=example[key]
        assert e['atomic_applications']==1 and e['target_value']==46
        s=e['requester']
        assert s['policy']=='atomic_return_required/1' and s['atomic_credit']==1 and not s['source_held']
        assert all(o['completed'] for o in s['operations']) and s['operations'][1]['old_value']==41
    assert example['healthy']['ambiguous_plain_ack_rejected'] is None
    assert example['lost_feedback']['ambiguous_plain_ack_rejected'] and example['lost_feedback']['source_held_before_atomic_reply']==[1]


def verify(run, output):
    upstream=run/'upstream'
    identity=json.loads((upstream/'identity.json').read_bytes())
    assert identity['commit']=='adc218676eef25575469234709c2d87185ca223a'
    for row in identity['sources']:
        raw=(upstream/row['local']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==row['sha256']
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==row['git_blob']
    oracle=run/'oracle-v4'
    branches=json.loads((oracle/'source-branches.json').read_bytes())['branches']
    for row in branches:
        text=(upstream/'sources'/row['file']).read_text()
        if row['function'].startswith('case'):
            start=text.index(row['function'],text.index('int rxe_completer('))
            end_label='case COMPST_COMP_ACK:' if row['function']=='case COMPST_WRITE_SEND:' else 'case COMPST_DONE:'
            end=text.index(end_label,start+len(row['function']))
            raw=text[start:end]
        else:
            raw='\n'.join(text.splitlines()[row['first']-1:row['last']])
        assert hashlib.sha256(raw.encode()).hexdigest()==row['sha256'],row
        compiled=(oracle/('responder.c' if row['file']=='rxe_resp.c' else 'completer.c')).read_text()
        assert raw in compiled
    rows=[json.loads(s) for s in (run/'oracle-run-v2-monitor/stdout.log').read_text().splitlines()]
    assert (run/'oracle-run-monitor/stdout.log').read_bytes()==(run/'oracle-run-v2-monitor/stdout.log').read_bytes()
    unit=json.loads((run/'unit-monitor/probe.json').read_bytes())
    assert unit['status']=='completed' and unit['child_exit_code']==0
    assert 'Ran 11 tests' in (run/'unit-monitor/stderr.log').read_text()
    example=json.loads((run/'example.json').read_bytes())
    check(rows,example)
    mutations=[]
    for label,case,key,value in [('invented_return','duplicate_write_after_lost_atomic_ack','returned_value',41),
                                 ('second_add','lost_reply_replay','target_value',51),
                                 ('missing_copy','healthy','atomic_copies',0)]:
        broken=deepcopy(rows)
        next(r for r in broken if r['case']==case)[key]=value
        try:check(broken,example)
        except AssertionError:mutations.append(label)
        else:raise AssertionError('accepted mutation: '+label)
    broken=deepcopy(example);broken['lost_feedback']['source_held_before_atomic_reply']=[]
    try:check(rows,broken)
    except AssertionError:mutations.append('early_source_release')
    else:raise AssertionError('accepted early release')
    value=dict(passed=True,pinned_sources=len(identity['sources']),unchanged_upstream_functions=13,
        unchanged_completer_cases=2,source_branch_cases=5,reference_unit_tests=11,
        rejected_mutations=mutations,network_runs=0,full_RXE_executed=False,hardware_calibrated=False,
        exact_RXE_reference_equivalence=False,known_branch_difference_retained=True)
    output.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();verify(a.run,a.output)
