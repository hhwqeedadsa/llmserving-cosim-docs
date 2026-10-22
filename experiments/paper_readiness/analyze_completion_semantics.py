"""Paired completion-event audit using public transport and independently checked packet facts."""
import argparse
from collections import Counter,defaultdict
import csv
from fractions import Fraction
import hashlib
from itertools import combinations
import json
from pathlib import Path

def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def sign(value):return (value>0)-(value<0)


def packet_rows(root):
    summary=load(root/'analysis/summary.json');audit=load(root/'checked/verification.json')
    assert audit['passed'] and audit['summary_sha256']==sha(root/'analysis/summary.json')
    assert summary['packets_csv_sha256']==sha(root/'analysis/packets.csv')
    by_case=defaultdict(list)
    with (root/'analysis/packets.csv').open() as stream:
        for row in csv.DictReader(stream):
            converted={k:(v if k in ('case','path') else int(v)) for k,v in row.items()}
            by_case[row['case']].append(converted)
    identities={row['case']:row['result_sha256'] for row in audit['cases']}
    return by_case,identities


def audit_work(result,inputs,packets,event):
    work={w['id']:w for w in result['persistent_transport']['work']};descriptors={w['id']:w for w in inputs['work']}
    assert work.keys()==descriptors.keys()
    by_work=defaultdict(list)
    for packet in packets:by_work[packet['work']].append(packet)
    waits=[];resource_owners=[]
    for identity,w in work.items():
        descriptor=descriptors[identity];parents=descriptor['after']
        assert all(p['event']==event for p in parents)
        ready=max((work[p['work']][p['event']+'_ns'] for p in parents),default=0)
        assert ready==w['ready_ns']
        previous=[p for p in work.values() if p['stream']==w['stream'] and p['end']<=w['begin']]
        owner=max(previous,key=lambda p:p['end']) if previous else None
        release=owner['acked_ns'] if owner else 0
        assert w['published_ns']==max(ready,release)
        assert w['published_ns']<=w['accepted_ns']<=w['acked_ns']
        if w['published_ns']>ready:
            assert owner and owner['acked_ns']==w['published_ns']
            waits.append(w['published_ns']-ready)
            resource_owners.append(dict(work=identity,owner_work=owner['id'],ready_ns=ready,
                released_ns=release,wait_ns=release-ready))
    latest=max(w['acked_ns'] for w in work.values());finals=[w['id'] for w in work.values() if w['acked_ns']==latest]
    chain=[]
    def trace(identity,finish_event):
        w=work[identity];parents=descriptors[identity]['after']
        candidates=[p for p in parents if work[p['work']][p['event']+'_ns']==w['ready_ns']]
        if parents:
            assert candidates
            selected=min(candidates,key=lambda p:p['work']);trace(selected['work'],selected['event'])
        else:assert w['ready_ns']==0
        data=[p for p in by_work[identity] if p['protocol']==17];tail=max(data,key=lambda p:p['last_rx_ns'])
        assert tail['last_rx_ns']==w['accepted_ns']
        components=dict(source_queue_credit_wait_ns=w['published_ns']-w['ready_ns'],
            source_tail_after_publication_ns=tail['first_tx_ns']-w['published_ns'],
            data_serialization_ns=tail['serialization_ns'],data_propagation_ns=tail['propagation_ns'],
            data_switch_wait_ns=tail['switch_wait_ns'])
        ack_uid=None
        if finish_event=='acked':
            ack=min((p for p in by_work[identity] if p['protocol']==252 and p['sequence']>=w['end']),key=lambda p:p['last_rx_ns'])
            assert ack['last_rx_ns']==w['acked_ns'];ack_uid=ack['uid']
            components.update(ack_generation_gap_ns=ack['first_tx_ns']-tail['last_rx_ns'],
                ack_serialization_ns=ack['serialization_ns'],ack_propagation_ns=ack['propagation_ns'],ack_switch_wait_ns=ack['switch_wait_ns'])
        assert min(components.values())>=0 and sum(components.values())==w[finish_event+'_ns']-w['ready_ns']
        chain.append(dict(work=identity,event=finish_event,start_ns=w['ready_ns'],end_ns=w[finish_event+'_ns'],
            tied_parent_events=candidates,data_uid=tail['uid'],ack_uid=ack_uid,components=components))
    trace(min(finals),'acked')
    assert chain[0]['start_ns']==0 and chain[-1]['end_ns']==latest
    assert all(left['end_ns']==right['start_ns'] for left,right in zip(chain,chain[1:]))
    assert sum(sum(s['components'].values()) for s in chain)==latest
    components=Counter()
    for segment in chain:components.update(segment['components'])
    windows=[]
    for step in range(4):
        selected=[w for w in work.values() if w['id']//16==step];ready={w['ready_ns'] for w in selected};assert len(ready)==1
        windows.append(dict(step=step,ready_ns=ready.pop(),published_first_ns=min(w['published_ns'] for w in selected),
            published_last_ns=max(w['published_ns'] for w in selected),accepted_ns=max(w['accepted_ns'] for w in selected),
            acked_ns=max(w['acked_ns'] for w in selected),
            prior_window_unacked_at_ready=sum(p['acked_ns']>selected[0]['ready_ns'] for p in work.values() if p['id']//16==step-1)))
    return dict(accepted_ns=max(w['accepted_ns'] for w in work.values()),acked_ns=latest,finish_ns=result['finish_ns'],
        windows=windows,window_ack_duration_sum_ns=sum(w['acked_ns']-w['ready_ns'] for w in windows),
        publication_wait_work_count=len(waits),max_publication_wait_ns=max(waits,default=0),
        publication_waits=resource_owners,application_predecessor_witness=chain,chain_components=dict(components),
        final_ack_tied_work_ids=finals,
        chain_scope='one exact application-predecessor witness; credit waits identify their source ACK owner, not a complete resource-causal DAG or removable delay')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','candidate','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();baseline,candidate=args.baseline,args.candidate
    # The saved absolute directory is provenance, not identity: verified
    # checkpoints may be restored at a different path.
    prepared=load(candidate/'baseline.json')
    assert prepared['observed_index_sha256']==sha(baseline/'observed/index.json')
    assert prepared['packet_verification_sha256']==sha(baseline/'checked/verification.json')
    assert load(baseline/'run/execution.json')['backend_sha256']==load(candidate/'run/execution.json')['backend_sha256']
    indexes=[load(root/'observed/index.json') for root in (baseline,candidate)];cfg=indexes[0]['config']
    assert indexes[1]['config']==cfg and indexes[1]['dependency_event']=='accepted'
    assert indexes[0].get('dependency_event','acked')=='acked'
    assert indexes[0]['artifacts'].keys()==indexes[1]['artifacts'].keys()
    for root,index in zip((baseline,candidate),indexes):
        for name,info in index['artifacts'].items():assert sha(root/'observed'/name)==info['sha256']
    for name in indexes[0]['artifacts']:
        if not name.startswith('work-'):
            assert (baseline/'observed'/name).read_bytes()==(candidate/'observed'/name).read_bytes()
    expected_work={};actual_work={}
    for condition in cfg['placement_conditions']:
        old=load(baseline/'observed'/f'work-{condition}.json');new=load(candidate/'observed'/f'work-{condition}.json')
        copy=json.loads(json.dumps(new))
        for work in copy['work']:
            for dependency in work['after']:
                assert dependency['event']=='accepted';dependency['event']='acked'
        assert copy==old;expected_work[condition]=old;actual_work[condition]=new
    packet_sets=[packet_rows(root) for root in (baseline,candidate)];cases=[];checks=Counter()
    for network in cfg['network_profiles']:
        assert (baseline/'run'/('topology-'+network)/'topology.json').read_bytes()==(candidate/'run'/('topology-'+network)/'topology.json').read_bytes()
        for condition in cfg['placement_conditions']:
            case=network+'-'+condition;results=[]
            for root,(_,identities) in zip((baseline,candidate),packet_sets):
                path=root/'run'/case/'result.json';assert sha(path)==identities[case]
                results.append(load(path))
            assert prepared['result_identities'][case]['sha256']==sha(baseline/'run'/case/'result.json')
            for result in results:assert result['passed'] and result['drained'] and result['fault_drops']==0
            # Times and UIDs may change; every physical packet's identity/shape/path remains.
            signature=lambda row:tuple(row[k] for k in ('work','protocol','sequence','src','dst','model_bytes','payload_bytes','path'))
            old_packets,new_packets=(data[0][case] for data in packet_sets)
            assert Counter(map(signature,old_packets))==Counter(map(signature,new_packets))
            checks['matched_packet_shapes_and_paths']+=len(old_packets)
            columns=('node','port','peer','rate_bps','model_tx_bytes','data_tx_bytes','control_tx_bytes','data_packets')
            port_signatures=[{(p['node'],p['port']):tuple(p[k] for k in columns) for p in result['ports']} for result in results]
            assert port_signatures[0]==port_signatures[1];checks['matched_physical_ports']+=len(port_signatures[0])
            global_bound=max(Fraction(p['model_tx_bytes']*8*10**9,p['rate_bps']) for p in results[0]['ports'])
            old=audit_work(results[0],expected_work[condition],old_packets,'acked')
            new=audit_work(results[1],actual_work[condition],new_packets,'accepted')
            assert old['window_ack_duration_sum_ns']==old['acked_ns']
            assert global_bound<=min(old['acked_ns'],new['acked_ns'])
            cases.append(dict(case=case,network=network,condition=condition,whole_run_port_service_bound_ns=float(global_bound),
                baseline=old,candidate=new,ack_difference_ns=old['acked_ns']-new['acked_ns'],
                relative_ack_difference=(old['acked_ns']-new['acked_ns'])/old['acked_ns'],
                acknowledged_intervals_overlap_amount_ns=new['window_ack_duration_sum_ns']-new['acked_ns']))
    comparisons=[]
    for network in cfg['network_profiles']:
        selected={r['condition']:r for r in cases if r['network']==network}
        for left,right in combinations(cfg['placement_conditions'],2):
            old=sign(selected[left]['baseline']['acked_ns']-selected[right]['baseline']['acked_ns'])
            new=sign(selected[left]['candidate']['acked_ns']-selected[right]['candidate']['acked_ns'])
            comparisons.append(dict(network=network,left=left,right=right,baseline_order=old,candidate_order=new,changed=old!=new))
    args.output.mkdir(exist_ok=False)
    save(args.output/'comparison.json',dict(cases=cases,comparisons=comparisons,checks=dict(checks),
        ranking_changes=sum(r['changed'] for r in comparisons),cases_count=len(cases),
        publication_wait_work_count=sum(r['candidate']['publication_wait_work_count'] for r in cases),
        max_publication_wait_ns=max(r['candidate']['max_publication_wait_ns'] for r in cases),
        scope='completion-rule sensitivity of independent byte writes; no GPU visibility/modern-library validity or deployable speedup claim'))
    print(json.dumps(dict(passed=True,cases=len(cases),ranking_changes=sum(r['changed'] for r in comparisons),**checks)))


if __name__=='__main__':main()
