"""Independent original-input/DAG/wire/path/queue and timing evidence checks."""
import argparse
from copy import deepcopy
from pathlib import Path
from experiments.nic_wakeup_effect.stage import load,save,sha
from experiments.local_fault_baselines.checks import check_case,verify_sources
from experiments.local_fault_baselines.routing import check_routes
from experiments.local_fault_baselines.verify_mechanism import check as check_mechanism
from experiments.reordering_baseline.verify import grace
from experiments.reordering_baseline.verify_train import check as check_train


def verify(s,a):
    assert all(sha(s/'inputs'/n)==h for n,h in load(s/'inputs/provenance.json')['files'].items())
    waves=verify_sources(s/'inputs/source-inputs',s/'inputs');reports={};raw={}
    for name,summary in a['cases'].items():
        version,case=name.split('-',1);plan=s/('inputs/baseline' if version=='old' else 'planned')/case
        r=load((s/'inputs/baseline' if version=='old' else s/'network')/case/'result.json')
        w=load(plan/'work.json');m=load(plan/'mapping.json');p=load(s/'inputs/observed'/(case+'-protocol.json'))
        assert w['transport_recovery']['reordering']==dict(mode='nack_grace/1',wait_ns=16000,max_events=30000)
        reports[name]=dict(original_graph_and_transport=check_case(waves,w,m,r,summary,p),
            routes=check_routes(load(s/'topology/topology.json'),p,w,r),mechanism=check_mechanism(r,summary,a['mechanisms'][name]),grace=grace(r))
        if case!='healthy':check_train(r,a['work99_source_trains'][name])
        raw[name]=(r,w,m,p,summary)
    for case in ('healthy','live-delayed','live-instant'):
        for name in ('work.json','mapping.json'):
            assert (s/'planned'/case/name).read_bytes()==(s/'inputs/baseline'/case/name).read_bytes()
    assert (s/'topology/topology.json').read_bytes()==(s/'inputs/baseline/topology.json').read_bytes()
    for pair in a['contrasts']:
        x,y=(a['cases'][pair[k]] for k in ('before','after'));old={w['work']:w for w in x['writes']};new={w['work']:w for w in y['writes']}
        assert pair['group_delta_ns']==y['group_completion_ns']-x['group_completion_ns']
        assert pair['extra_source_payload_bytes']==y['tx_payload_bytes']-x['tx_payload_bytes']
        assert {w['work'] for w in pair['writes']}==set(old)==set(new)
        for w in pair['writes']:
            left,right=old[w['work']],new[w['work']]
            assert w['receive_delta_ns']==right['accepted_ns']-left['accepted_ns']==w['ready_delta_ns']+w['witness_send_after_ready_delta_ns']+w['witness_queue_delta_ns']+w['witness_transit_delta_ns']
            assert w['ack_delta_ns']==right['acked_ns']-left['acked_ns']
    rejected=[]
    for mode in ('ack_time','queue_wait','feedback_hex','grace_deadline','dependency','service_owner','retry_outcome','train_due','train_idle','train_service'):
        r,w,m,p,summary=deepcopy(raw['fixed-live-instant']);mech=deepcopy(a['mechanisms']['fixed-live-instant']);tr=deepcopy(a['work99_source_trains']['fixed-live-instant'])
        if mode=='ack_time':r['persistent_transport']['work'][0]['acked_ns']+=1
        elif mode=='queue_wait':summary['writes'][0]['witness']['queue_wait_ns']+=1
        elif mode=='feedback_hex':next(e for e in r['persistent_transport']['selective_repeat']['events'] if e['kind']=='feedback_received')['extension_hex']='00'*16
        elif mode=='grace_deadline':next(e for e in r['persistent_transport']['nack_grace']['events'] if e['after']['deadline_ns']>=0)['after']['deadline_ns']+=1
        elif mode=='dependency':next(x for x in w['work'] if x['after'])['after']=[]
        elif mode=='service_owner':
            h=next(h for w in mech['writes'] for h in w['hops'] if h['service_by_stream_attempt_ns']);h['service_by_stream_attempt_ns'][next(iter(h['service_by_stream_attempt_ns']))]+=1
        elif mode=='retry_outcome':
            bucket=mech['repeated_payload_by_initial_copy_outcome'];sid=next(iter(bucket));bucket[sid][next(iter(bucket[sid]))]+=1
        else:
            key={'train_due':'sampled_next_avail_ns','train_idle':'idle_after_eligibility_ns','train_service':'physical_service_after_eligibility_ns'}[mode];tr['gaps'][0][key]+=1
        try:
            check_case(waves,w,m,r,summary,p);check_mechanism(r,summary,mech);grace(r);check_train(r,tr)
        except (AssertionError,KeyError,IndexError,ValueError):rejected.append(mode)
        else:raise AssertionError('mutation accepted: '+mode)
    return dict(passed=True,cases=reports,all_original_nodes_dependencies_and_writes=True,
        work_mapping_topology_byte_identical=True,all_actual_enqueue_bfs_hash=True,
        work99_four_trains_independently_checked=True,mutations_rejected=rejected,network_executions=0,
        scope='six application cases; actual packet and queue checks, source interval accounting; no fixture/platform decoder or external library reads')


def main():
    p=argparse.ArgumentParser()
    for k in ('stage','analysis','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();assert not a.output.exists();save(a.output,verify(a.stage,load(a.analysis)))
    print('all six cases independently verified')


if __name__=='__main__':main()
