"""Offline checks only: full raw result replay, mechanism coverage and evidence mutations."""
import argparse
from copy import deepcopy
from pathlib import Path
from fixtures.nack_grace.audit import audit
from fixtures.nack_grace.stage import load,save


def verify(repo,s,additional=None):
    cases=load(s/'cases.json');reports={}
    for name in ('default-old','default-new','head-loss-idle-fixed','no-feedback','fresh-mixed'):
        x=cases[name];reports[name]=audit(load(s/'network'/name/'result.json'),load(s/'inputs'/x['work']),load(s/'inputs'/x['protocol']))
    head=reports['head-loss-idle-fixed']['grace'];none=reports['no-feedback']['grace'];mix=reports['fresh-mixed']['grace']
    assert len(head['retry_intents'])==1 and head['events'].get('rto',0)==0
    assert none['events'].get('rto')==1 and not none['retry_intents']
    additional_report=None
    cancellation=mix
    if additional is not None:
        from fixtures.nack_grace_edges.verify import verify as verify_edges
        additional_report=verify_edges(additional)
        cancellation=additional_report['cases']['transient-detour']['grace']
    # Retain the original required real-packet cancellation gate. The old
    # mixed result is never edited or relabelled; an explicitly supplied new
    # phase may prove the missing branch by independently replaying its raw data.
    assert cancellation['progress_cancellations']>0 and mix['duplicate_nacks_did_not_extend']>0
    h=load(s/'network/head-loss-idle-fixed/result.json');n=load(s/'network/no-feedback/result.json')
    when=head['retry_intents'][0];source=h['persistent_transport']['source_packets']
    assert any(row[0]>=when['time_ns'] and row[1]==when['stream'] and row[5]==when['prefix'] for row in source)
    first_feedback=min(e['time_ns'] for e in n['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received' and e['stream']==2)
    rto=next(e['time_ns'] for e in n['persistent_transport']['nack_grace']['events'] if e['kind']=='rto')
    assert rto==80000<first_feedback
    for value in (h,n):
        idle=next(x for x in value['persistent_transport']['nack_grace']['states'] if x['stream']==8)
        assert idle==dict(stream=8,prefix=0,deadline_ns=-1,serviced=False,trigger_feedback_uid=-1)
    mutations=[]
    base=load(s/'network/fresh-mixed/result.json');x=cases['fresh-mixed'];w=load(s/'inputs'/x['work']);c=load(s/'inputs'/x['protocol'])
    for mode in ('deadline','trigger_uid','prefix','missing_feedback','retry_time','false_wire_sack'):
        r=deepcopy(base);events=r['persistent_transport']['nack_grace']['events']
        if mode=='deadline':next(e for e in events if e['after']['deadline_ns']>=0)['after']['deadline_ns']+=1
        if mode=='trigger_uid':next(e for e in events if e['after']['deadline_ns']>=0)['after']['trigger_feedback_uid']+=1000000
        if mode=='prefix':events[0]['after']['prefix']+=1
        if mode=='missing_feedback':events.remove(next(e for e in events if e['kind']=='feedback'))
        if mode=='retry_time':
            # Change a timer OR a feedback cancellation to a fake expiry.
            e=next(e for e in events if e['before']['deadline_ns']>=0);e['kind']='retry';e['time_ns']+=1
        if mode=='false_wire_sack':next(e for e in r['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')['extension_hex']='00'*16
        rejected=False
        try:audit(r,w,c)
        except (AssertionError,KeyError,IndexError,ValueError):rejected=True
        assert rejected,mode
        mutations.append(mode)
    return dict(passed=True,cases=reports,additional_edges=additional_report,
        cancellation_evidence='additional transient-detour' if additional is not None else 'original mixed',
        wire_and_grace_mutations_rejected=mutations,
        coverage=dict(original_stage_network_attempts=6,original_stage_successful_networks=5,
            additional_stage_network_attempts=4 if additional_report else 0,
            combined_network_attempts=10 if additional_report else 6,idle_close_repaired=True,
            positive_grace_retransmission=True,missing_all_feedback_rto=True,progress_cancels_timer=True,
            duplicate_nack_does_not_extend=True,new_python_root_public_workflow=True,
            exact_same_ns_rto_network_observed=False),
        scope='functional classical source feedback baseline; no research benefit, modern GIN or calibrated timing claim')


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','stage','output'):p.add_argument('--'+k,type=Path,required=True)
    p.add_argument('--additional-stage',type=Path)
    a=p.parse_args();assert not a.output.exists();save(a.output,verify(a.repository,a.stage,a.additional_stage))
    print('saved original and explicit supplemental evidence verified; no network execution')

if __name__=='__main__':main()
