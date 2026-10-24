"""Report measured coverage without changing or bypassing strict acceptance gates."""
import argparse
from copy import deepcopy
from pathlib import Path
from fixtures.nack_grace.audit import audit
from fixtures.nack_grace.stage import load,save


def inspect(s):
    cases=load(s/'cases.json');reports={}
    for name in ('default-old','default-new','head-loss-idle-fixed','no-feedback','fresh-mixed'):
        x=cases[name];reports[name]=audit(load(s/'network'/name/'result.json'),load(s/'inputs'/x['work']),load(s/'inputs'/x['protocol']))
    head=reports['head-loss-idle-fixed']['grace'];none=reports['no-feedback']['grace'];mix=reports['fresh-mixed']['grace']
    h=load(s/'network/head-loss-idle-fixed/result.json');n=load(s/'network/no-feedback/result.json')
    when=head['retry_intents'][0]
    source=h['persistent_transport']['source_packets']
    assert any(row[0]>=when['time_ns'] and row[1]==when['stream'] and row[5]==when['prefix'] for row in source)
    first_feedback=min(e['time_ns'] for e in n['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received' and e['stream']==2)
    assert next(e['time_ns'] for e in n['persistent_transport']['nack_grace']['events'] if e['kind']=='rto')==80000<first_feedback
    for value in (h,n):
        idle=next(x for x in value['persistent_transport']['nack_grace']['states'] if x['stream']==8)
        assert idle==dict(stream=8,prefix=0,deadline_ns=-1,serviced=False,trigger_feedback_uid=-1)
    # Keep the original strict validator intact: it still fails on missing
    # cancellation coverage. These checks reject changed evidence, not certify
    # the missing branch or substitute for a future packet-level case.
    mutations=[]
    base=load(s/'network/fresh-mixed/result.json');x=cases['fresh-mixed'];w=load(s/'inputs'/x['work']);c=load(s/'inputs'/x['protocol'])
    for mode in ('deadline','trigger_uid','prefix','missing_feedback','retry_time','false_wire_sack'):
        r=deepcopy(base);events=r['persistent_transport']['nack_grace']['events']
        if mode=='deadline':next(e for e in events if e['after']['deadline_ns']>=0)['after']['deadline_ns']+=1
        if mode=='trigger_uid':next(e for e in events if e['after']['deadline_ns']>=0)['after']['trigger_feedback_uid']+=1000000
        if mode=='prefix':events[0]['after']['prefix']+=1
        if mode=='missing_feedback':events.remove(next(e for e in events if e['kind']=='feedback'))
        if mode=='retry_time':next(e for e in events if e['kind']=='retry')['time_ns']+=1
        if mode=='false_wire_sack':next(e for e in r['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')['extension_hex']='00'*16
        rejected=False
        try:audit(r,w,c)
        except (AssertionError,KeyError,IndexError,ValueError):rejected=True
        assert rejected,mode
        mutations.append(mode)
    return dict(platform_acceptance_complete=False,actual_network_attempts=6,successful_networks=5,
        saved_wire_and_source_replays_passed=True,cases=reports,mutations_rejected=mutations,
        coverage=dict(positive_grace_retry=len(head['retry_intents'])==1 and head['events'].get('rto',0)==0,
            no_feedback_rto=none['events'].get('rto')==1 and not none['retry_intents'],
            duplicate_nack_not_extended=mix['duplicate_nacks_did_not_extend']>0,
            progress_cancel_in_actual_network=mix['progress_cancellations']>0,
            native_idle_close_repaired=True,new_root_public_workflow=load(s/'fresh.json')['passed']),
        missing=['actual packet-level feedback progress cancels a pending timer',
            'same-ns RTO/expiry and source exhaustion cancellation have CPU/state coverage only'],
        strict_verifier='verify.py still rejects the missing required cancellation; no seventh network authorized in this closed stage',
        scope='measured functionality only; no application benefit, novelty, modern GIN or calibration claim')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,inspect(a.stage))
    print('saved evidence replayed; actual cancellation coverage remains missing')

if __name__=='__main__':main()
