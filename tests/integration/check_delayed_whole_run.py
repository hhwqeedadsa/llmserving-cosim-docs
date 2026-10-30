"""Relocate actual delayed networks and reject forged timer/skip/KV claims."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from moe_ub_cosim.scaleout_verify import verify


def hashes(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();run=a.run.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    before=hashes(run);moved=out/'relocated';shutil.copytree(run,moved);verified=verify(moved)
    names=['frontend.json','result.json'];original={name:(moved/name).read_bytes() for name in names}
    f=json.loads(original['frontend.json']);r=json.loads(original['result.json'])
    skipped=[q['id'] for q in f['requests'] if q['state']=='NOT_STARTED'];rejected=[]
    cases=['timer_event','timer_pending','decision_deadline','reserved_KV','released_KV','early_result','foreign_group']
    cases+=['fake_ITL','fake_token','fake_finish'] if skipped else ['late_start','fake_skip']
    for name in cases:
        ff,rr=deepcopy(f),deepcopy(r)
        if name=='timer_event':rr['service']['control_wakeup']['events'][-1]['time_ns']+=1
        elif name=='timer_pending':rr['service']['control_wakeup']['pending_deadline_ns']=70000
        elif name=='decision_deadline':ff['transcript'][0]['reply']['wake_at_ns']+=1
        elif name=='reserved_KV':ff['standby_memory']['initial_reserved_bytes']-=16
        elif name=='released_KV':ff['standby_memory']['releases'][0]['time_ns']-=1
        elif name=='early_result':ff['whole_call_replication']['result_ready_ns']-=1
        elif name=='foreign_group':ff['history'][0]['group_index']=1
        elif name=='fake_ITL':next(q for q in ff['requests'] if q['id'] in skipped)['intertoken_ns']=[1]
        elif name=='fake_token':next(q for q in ff['requests'] if q['id'] in skipped)['computed_tokens']+=1
        elif name=='fake_finish':next(q for q in ff['requests'] if q['id'] in skipped)['scheduler_state']='FINISHED'
        elif name=='late_start':next(h for h in ff['history'] if h['group_index']==1)['start_ns']+=1
        else:ff['whole_call_replication']['calls']['segment0']['attempts']['replica']['skipped_ns']=70000
        (moved/'frontend.json').write_text(json.dumps(ff));(moved/'result.json').write_text(json.dumps(rr))
        try:
            try:verify(moved)
            except ValueError as error:rejected.append(dict(case=name,error=str(error)))
            else:raise AssertionError('forged delayed execution accepted: '+name)
        finally:
            for file,raw in original.items():(moved/file).write_bytes(raw)
    assert len(rejected)==len(cases) and before==hashes(run)==hashes(moved)
    report=dict(passed=True,public_verification=verified,relocated=True,originals_unchanged=True,
        skipped_request_ids=skipped,new_network_executions=0,rejected_mutations=rejected)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,rejected=len(rejected),skipped_requests=len(skipped))))

if __name__=='__main__':main()
