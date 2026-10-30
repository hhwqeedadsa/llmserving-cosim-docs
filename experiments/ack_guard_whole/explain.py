"""Retrospectively trace actual healthy gate dependencies and complete races."""
import argparse
import hashlib
import json
from pathlib import Path

def load(p):return json.loads(p.read_bytes())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def packet_path(result,uid):
    events=[p for p in result['packet_capture']['events'] if p[4]==uid]
    starts=[p for p in events if p[1]<128 and p[3]==0];ends=[p for p in events if p[1]<128 and p[3]==1]
    assert len(starts)==len(ends)==1
    pending={};hops=[]
    for p in events:
        if p[1]<128:continue
        if p[3]==1:assert p[1] not in pending;pending[p[1]]=p[0]
        elif p[3]==0:hops.append(dict(node=p[1],port=p[2],rx_ns=pending.pop(p[1]),tx_ns=p[0]))
    assert not pending
    residence=sum(h['tx_ns']-h['rx_ns'] for h in hops)
    return dict(source_ns=starts[0][0],destination_ns=ends[0][0],residence_ns=residence,
        nonresidence_ns=ends[0][0]-starts[0][0]-residence,hops=hops)

def message_paths(result,message):
    packets=[p for p in result['service_transport']['native']['source_packets']
        if p[1]==message['stream'] and p[5]<=message['end']-1<p[5]+p[6]]
    assert len(packets)==1,'healthy final packet retransmitted or missing'
    data=packet_path(result,packets[0][7])
    ack_events=[p for p in result['packet_capture']['events'] if p[1]==message['src'] and p[3]==1 and p[5]==message['stream']
        and p[6]==252 and p[7]>=message['end'] and p[0]==message['ack_ns']]
    assert ack_events
    ack=packet_path(result,ack_events[0][4])
    assert data['destination_ns']==message['receive_ready_ns'] and ack['destination_ns']==message['ack_ns']
    return dict(data=data,ack=ack)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True)
    p.add_argument('--analysis',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    frozen=load(a.frozen);analysis=load(a.analysis/'analysis.json');proofs={};races={};exact={}
    for condition in ('normal','local'):
        paths=[Path(frozen['inherited_cases']['control-'+condition]['output']),Path(frozen['new_cases']['guarded-'+condition]['output'])]
        results=[load(path/'result.json') for path in paths];times=[];messages=[]
        for path,result in zip(paths,results):
            events=[json.loads(line) for line in (path/'execution_events.jsonl').read_text().splitlines()]
            times.append({(e['graph_id'],e['rank'],e['node']):e['time_ns'] for e in events if e['kind']=='node_complete'})
            messages.append({m['id']:m for m in result['service_transport']['messages']})
        baseline_gates={(g['graph_id'],g['id']):g for g in results[0]['service']['phase_completion']['gates']}
        gates=[]
        for gate in results[1]['service']['phase_completion']['gates']:
            graph=gate['graph_id']
            if not graph.startswith('g2.'):continue
            original=baseline_gates[graph,gate['id']];wave=load(paths[1]/'graphs'/graph/'wave.json');tight=[]
            for member in gate['members']:
                if member['arrived_ns']!=gate['released_ns']:continue
                rank=member['rank'];nodes={n['id']:n for n in wave['rank_nodes'][str(rank)]};join=nodes[member['node']]
                for dep in join['deps']:
                    if times[1][graph,rank,dep]!=gate['released_ns']:continue
                    node=nodes[dep];before=times[0][graph,rank,dep];after=times[1][graph,rank,dep]
                    entry=dict(rank=rank,node=dep,kind=node['kind'],name=node['name'],
                        baseline_dependency_complete_ns=before,treatment_dependency_complete_ns=after,
                        completion_change_ns=after-before,baseline_gate_slack_ns=original['released_ns']-before)
                    assert after-before-entry['baseline_gate_slack_ns']==gate['released_ns']-original['released_ns']
                    if node['kind'] in ('SEND','RECV'):
                        mid=node['message']['id'];old,new=(v[mid] for v in messages)
                        field='ack_ns' if node['kind']=='SEND' else 'receive_ready_ns'
                        assert (old[field],new[field])==(before,after)
                        entry.update(message=mid,source_ready_change_ns=new['ready_ns']-old['ready_ns'])
                        before_path,after_path=(message_paths(r,m) for r,m in zip(results,(old,new)))
                        for kind in ('data','ack'):
                            b,t=before_path[kind],after_path[kind]
                            assert [(h['node'],h['port']) for h in b['hops']]==[(h['node'],h['port']) for h in t['hops']]
                            assert t['nonresidence_ns']==b['nonresidence_ns']
                            assert t['destination_ns']-b['destination_ns']==t['source_ns']-b['source_ns']+t['residence_ns']-b['residence_ns']
                        entry['packet_paths']=dict(baseline=before_path,treatment=after_path)
                    tight.append(entry)
            assert tight
            gates.append(dict(graph=graph,phase=gate['id'],baseline_release_ns=original['released_ns'],
                treatment_release_ns=gate['released_ns'],change_ns=gate['released_ns']-original['released_ns'],tight_predecessors=tight))
        assert len(gates)==8;proofs[condition]=gates
        fixed=load(Path(frozen['new_cases']['fixed-'+condition]['output'])/'result.json')
        exact[condition]=dict(fixed_and_guarded_packets_exact=fixed['packet_capture']==results[1]['packet_capture'],
            fixed_and_guarded_queue_events_exact=fixed['queue_events']==results[1]['queue_events'])
        call=analysis['cases']['guarded-'+condition]['calls']['target'];races[condition]=dict(winner=call['winner'],
            result_ready_ns=call['result_ready_ns'],primary=call['attempts']['primary'],replica=call['attempts']['replica'])
    output=dict(passed=True,healthy_gates=proofs,actual_races=races,fixed_guarded_network_comparison=exact,
        new_network_executions=0,analyzer_sha256=sha(Path(__file__)),
        scope='Retrospective actual tight predecessors and final DATA/ACK paths. All ties retained. Posthoc remaining times and paths never enter online guard; packet residence totals are not business savings.')
    with a.output.open('x') as f:json.dump(output,f,indent=2);f.write('\n')
    print(json.dumps(dict(passed=True,healthy_gates=16,exact=exact)))

if __name__=='__main__':main()
