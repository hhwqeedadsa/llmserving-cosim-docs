"""Original routes/DAG/wire/queues, source feedback reconstruction and mutations."""
import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path
from experiments.reordering_baseline.stage import load,save
from experiments.local_fault_baselines.checks import check_case,verify_sources
from experiments.local_fault_baselines.routing import check_routes
from experiments.local_fault_baselines.verify_mechanism import check as check_mechanism


def grace(result):
    t=result['persistent_transport'];g=t['nack_grace'];cfg=g['config'];states={s['id']:dict(prefix=0,due=-1,used=False,uid=-1) for s in t['streams']}
    feedback=[e for e in t['selective_repeat']['events'] if e['kind']=='feedback_received'];seen=0
    assert not any(e['kind']=='fast_retransmit' for e in t['selective_repeat']['events'])
    count=Counter();arms=cancels=0
    def external(x):return dict(prefix=x['prefix'],deadline_ns=x['due'],serviced=x['used'],trigger_feedback_uid=x['uid'])
    for e in g['events']:
        x=states[e['stream']];assert e['before']==external(x);kind=e['kind'];now=e['time_ns'];count[kind]+=1
        if kind=='feedback':
            f=feedback[seen];seen+=1
            assert (e['feedback_uid'],e['stream'],e['cumulative_ack'],now,e['nack'])==(f['packet_uid'],f['stream'],f['cumulative_ack'],f['time_ns'],f['ip_protocol']==253)
            if e['cumulative_ack']>x['prefix']:
                cancels+=x['due']>=0
                if x['due']>=0:assert now<=x['due']
                x.update(prefix=e['cumulative_ack'],due=-1,used=False,uid=-1)
            if e['nack'] and e['cumulative_ack']==x['prefix'] and not x['used'] and x['due']<0:
                x.update(due=now+cfg['wait_ns'],uid=e['feedback_uid']);arms+=1
            assert e['snd_una']==x['prefix']<=e['highest_sent_bytes']
        elif kind=='retry':
            assert x['due']==now and x['uid']>=0 and x['prefix']==e['snd_una']<e['highest_sent_bytes']
            assert e['rto_deadline_ns']<0 or now<e['rto_deadline_ns']
            x.update(due=-1,used=True,uid=-1)
        elif kind=='close':
            assert t['closed'];x.update(due=-1,uid=-1)
        else:raise AssertionError('unexpected timer/stop in these successful no-RTO cases')
        if kind!='feedback':assert e['feedback_uid']==e['cumulative_ack']==-1
        assert e['after']==external(x)
    assert seen==len(feedback) and g['states']==[dict(external(x),stream=sid) for sid,x in sorted(states.items())]
    assert all(x['due']==-1 and x['uid']==-1 for x in states.values())
    return dict(events=len(g['events']),arms=arms,progress_cancellations=cancels,retries=count['retry'],rtos=0)


def healthy_equal(before,after,libraries):
    values=deepcopy([before,after]);removed=values[1]['persistent_transport'].pop('nack_grace')
    assert removed['config']==dict(mode='nack_grace/1',wait_ns=16000,max_events=30000)
    assert values[1]['persistent_transport']['recovery']['config'].pop('reordering')==removed['config']
    # Both saved results contain exactly the same path spellings. Offline
    # verification must not resolve or read external runtime files; their hashes
    # are checked at execution/closeout, separately from result compatibility.
    assert values[0]['loaded_libraries']==values[1]['loaded_libraries']
    assert len(libraries)==8
    assert values[0]==values[1], 'healthy data/state/time changed outside declared new config and log'
    return dict(all_other_result_fields_equal=True,new_fields='nack_grace log and recovery.config.reordering',
        normalized='none; loaded-library path strings also identical; runtime identity checked separately')


def verify(s,a):
    waves=verify_sources(s/'inputs/source-inputs',s/'inputs');reports={};raw={}
    for name,summary in a['cases'].items():
        prefix,condition=name.split('-',1);directory=s/('inputs/baseline' if prefix=='immediate' else 'planned')/condition
        r=load((s/'inputs/baseline' if prefix=='immediate' else s/'network')/condition/'result.json')
        w=load(directory/'work.json');m=load(directory/'mapping.json');p=load(s/'inputs/observed'/(condition+'-protocol.json'))
        report=dict(original_graph_and_transport=check_case(waves,w,m,r,summary,p),
            routes=check_routes(load(s/'topology/topology.json'),p,w,r),mechanism=check_mechanism(r,summary,a['mechanisms'][name]))
        if prefix=='grace':
            assert w['transport_recovery']['reordering']==dict(mode='nack_grace/1',wait_ns=16000,max_events=30000)
            report['grace']=grace(r);assert report['grace']==a['grace'][name]
        else:assert 'nack_grace' not in r['persistent_transport']
        reports[name]=report;raw[name]=(r,w,m,p,summary)
    compatibility=healthy_equal(raw['immediate-healthy'][0],raw['grace-healthy'][0],load(s/'runtime.json')['libraries'])
    for pair in a['contrasts']:
        x,y=(a['cases'][pair[k]] for k in ('before','after'))
        assert pair['group_delta_ns']==y['group_completion_ns']-x['group_completion_ns']
        assert pair['extra_source_payload_bytes']==y['tx_payload_bytes']-x['tx_payload_bytes']
        old={w['work']:w for w in x['writes']};new={w['work']:w for w in y['writes']}
        assert {w['work'] for w in pair['writes']}==set(old)==set(new)
        for w in pair['writes']:
            left,right=old[w['work']],new[w['work']]
            assert w['receive_delta_ns']==right['accepted_ns']-left['accepted_ns']==w['ready_delta_ns']+w['witness_send_after_ready_delta_ns']+w['witness_queue_delta_ns']+w['witness_transit_delta_ns']
            assert w['ack_delta_ns']==right['acked_ns']-left['acked_ns']
    rejected=[]
    # Mutate raw data and reported mechanisms separately; never reexecute.
    for mode in ('ack_time','queue_wait','feedback_hex','grace_deadline','dependency','service_owner','retry_outcome'):
        r,w,m,p,summary=deepcopy(raw['grace-live-instant']);mechanism=deepcopy(a['mechanisms']['grace-live-instant'])
        if mode=='ack_time':r['persistent_transport']['work'][0]['acked_ns']+=1
        if mode=='queue_wait':summary['writes'][0]['witness']['queue_wait_ns']+=1
        if mode=='feedback_hex':next(e for e in r['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')['extension_hex']='00'*16
        if mode=='grace_deadline':next(e for e in r['persistent_transport']['nack_grace']['events'] if e['after']['deadline_ns']>=0)['after']['deadline_ns']+=1
        if mode=='dependency':next(x for x in w['work'] if x['after'])['after']=[]
        if mode=='service_owner':
            h=next(h for w in mechanism['writes'] for h in w['hops'] if h['service_by_stream_attempt_ns']);h['service_by_stream_attempt_ns'][next(iter(h['service_by_stream_attempt_ns']))]+=1
        if mode=='retry_outcome':
            sid=next(iter(mechanism['repeated_payload_by_initial_copy_outcome']));bucket=mechanism['repeated_payload_by_initial_copy_outcome'][sid]
            bucket[next(iter(bucket))]+=1
        caught=False
        try:
            check_case(waves,w,m,r,summary,p);check_mechanism(r,summary,mechanism);grace(r)
        except (AssertionError,KeyError,IndexError,ValueError):caught=True
        assert caught,mode;rejected.append(mode)
    return dict(passed=True,cases=reports,healthy_complete_result_compatibility=compatibility,mutations_rejected=rejected,
        all_original_nodes_and_dependencies=True,all_writes_and_receive_witness_queues=True,network_executions=0,
        scope='three new grace cases and three immutable immediate baselines; no fixture/platform decoder imports')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,verify(a.stage,load(a.analysis)));print('six complete application cases independently verified')

if __name__=='__main__':main()
