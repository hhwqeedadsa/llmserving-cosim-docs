"""Evidence tamper checks and fixed-source/result aggregation; zero networks."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from fixtures.native_callback_batches.stage import load,save,sha
from fixtures.native_callback_batches.verify import delivery
from fixtures.modern_native.audit import audit


def tamper(stage):
    root=stage/'batch-healthy';control=[json.loads(line) for line in (root/'control.jsonl').read_text().splitlines()]
    selected=next(i for i in range(0,len(control),2) if len(control[i]['message']['batches'])>1)
    rejected=[]
    def check(label,change):
        rows=deepcopy(control);r=load(root/'result.json');m=load(root/'modern-execution.json')
        change(rows,r,m)
        try:delivery(root,{'control.jsonl':rows,'result.json':r,'modern-execution.json':m})
        except (AssertionError,KeyError,TypeError,ValueError):rejected.append(label)
        else:raise AssertionError('corrupt delivery accepted: '+label)
    check('historical-advancement-time',lambda c,r,m:c[selected]['message']['batches'][0].update(now_ns=-1))
    check('flat-callback-order',lambda c,r,m:c[selected]['message']['events'].reverse())
    check('batch-boundary-order',lambda c,r,m:c[selected]['message']['batches'].reverse())
    check('suppressed-drain',lambda c,r,m:c[selected]['message']['batches'][0].update(drained=True))
    check('batch-budget',lambda c,r,m:c[selected]['message'].update(batches=c[selected]['message']['batches']*65))
    check('physical-counter',lambda c,r,m:r['session'].update(decisions=257))
    check('logical-counter',lambda c,r,m:m['callback_batches'].update(logical_steps=1))
    check('registration-time',lambda c,r,m:r['session']['registrations'][0].update(time_ns=1))
    check('submitted-content',lambda c,r,m:m['native_operations'][0].update(value=999))
    check('future-condition',lambda c,r,m:c[1]['message'].update(wake_groups=[[dict(operation=65535,event='completed')]]))
    changed=load(root/'result.json');changed['transactions']['target_events'][0]['body_hex']='ff'*8
    try:audit(root,{'result.json':changed})
    except (AssertionError,KeyError,ValueError):rejected.append('target-memory-bytes')
    else:raise AssertionError('corrupt target accepted')
    save(stage/'tamper.json',dict(verified=True,rejected=rejected,network_executions=0))


def aggregate(repo,stage):
    identity=load(stage/'identity.json');runtime=load(stage/'runtime-identity.json')
    assert all(sha(repo/name)==digest for name,digest in identity['sources'].items())
    assert all(sha(Path(name))==digest for name,digest in identity['unchanged_objects'].items())
    assert all(sha(stage/'runtime'/name)==digest for name,digest in runtime['files'].items())
    assert all(sha(stage/name)==digest for name,digest in load(stage/'inputs.json').items())
    cases={case:load(stage/(case+'-verification.json')) for case in ('default-healthy','batch-healthy','batch-fault','four-routes')}
    assert all(value['verified'] for value in cases.values()) and load(stage/'tamper.json')['verified']
    assert cases['four-routes']['application']['modern_events']==566
    assert cases['four-routes']['application']['modern_operations']==118
    assert cases['four-routes']['delivery']['physical_decisions']<=256
    assert not cases['four-routes']['capture_complete']
    save(stage/'verification.json',dict(verified=True,cases=cases,tamper=load(stage/'tamper.json'),
        original_four_rank_call_complete=True,complete_four_rank_physical_capture=False,
        fixed_native_commit=identity['actual_native_commit'],fixed_streams_fabric_eight_libraries=True,
        platform_sources_verified=True,original_inputs_and_failure_preserved=True,overall_goal_status='in_progress'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--action',choices=['tamper','aggregate'],required=True);a=p.parse_args()
    if a.action=='tamper':tamper(a.stage)
    else:aggregate(a.repository,a.stage)


if __name__=='__main__':main()
