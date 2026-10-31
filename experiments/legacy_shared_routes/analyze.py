"""Read actual execution, audited groups and bounded switch packet observations."""
import argparse
from collections import Counter,defaultdict,deque
import gc
import hashlib
import json
from pathlib import Path

if not __debug__:raise RuntimeError('Do not run analysis with -O')
load=lambda p:json.loads(Path(p).read_bytes())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()


def critical_predecessors(work,legacy):
    events=work['events'];times=legacy['event_times_ns']
    finals=[i for i,e in enumerate(events) if e[0]=='kernel_complete']
    per_rank=[max(times[i] for i in finals if events[i][1]==rank) for rank in range(work['ranks'])]
    maximum=max(per_rank);last=[i for i in finals if times[i]==maximum]
    chosen=min(last);chain=[];seen=set()
    while True:
        assert chosen not in seen;seen.add(chosen)
        name,rank,deps,op_id,attrs=events[chosen]
        latest=max((times[d] for d in deps),default=None)
        tied=[d for d in deps if times[d]==latest]
        row=dict(event=chosen,name=name,rank=rank,time_ns=times[chosen],latest_predecessors=tied,
                 latest_predecessor_ns=latest,operation_id=op_id)
        if op_id is not None:
            row.update(operation=work['operations'][op_id],actual_operation=legacy['operations'][op_id],
                       actual_columns=legacy['operation_columns'])
        chain.append(row)
        if not tied:break
        chosen=min(tied)
    return dict(per_rank_kernel_complete_ns=per_rank,last_kernel_complete_ns=maximum,
        final_event_ties=last,selected_latest_predecessor_chain=chain,
        scope='One deterministic latest-predecessor chain; ties retained locally; not a unique path or removable-delay bound')


def observations(result,work,topology):
    capture=result['packet_capture'];assert capture['complete'] and capture['omitted_events']==0
    index={key:i for i,key in enumerate(capture['columns'])}
    assert capture['columns']==['time_ns','node','port','event','packet_uid','message_id','ip_protocol',
                                'sequence','model_bytes','ecn','egress_queue_bytes','data_payload_bytes']
    ranges=[];offset=0
    for group in work['groups']:
        size=group['execution']['limits']['streams'];ranges.append((offset,offset+size));offset+=size
    def owner(stream):
        for group,(begin,end) in enumerate(ranges):
            if begin<=stream<end:return group
        return -1
    links={}
    for link in topology['links']:
        for node,port,peer in [(link['src'],link['src_port'],link['dst']),(link['dst'],link['dst_port'],link['src'])]:
            links[node,port]=peer
    ports={};pending=defaultdict(deque);unmatched=0;witnesses=[[],[]];exposure=[Counter(),Counter()]
    unknown=Counter();paired=0
    for row in capture['events']:
        t,node,port,event,uid,stream,proto,seq,size,_,queue_bytes,_=row
        group=owner(stream)
        if group<0:unknown[str(proto)]+=1;continue
        if (node,port) in ((128,17),(132,1)):
            exposure[group][f'event_{event}_protocol_{proto}']+=1
            if 4000<=t<24000:exposure[group][f'during_outage_event_{event}_protocol_{proto}']+=1
        if node<128:continue
        key=(node,port,group)
        if key not in ports:ports[key]=dict(node=node,port=port,peer=links[node,port],group=group,
            tx_packets=0,tx_model_bytes=0,data_tx_packets=0,control_tx_packets=0,
            matched_dequeues=0,sum_residence_packet_ns=0,max_residence_ns=0,max_enqueue_queue_bytes=0)
        stats=ports[key]
        packet_key=(node,port,uid,stream,proto,seq)
        if event==0:
            stats['tx_packets']+=1;stats['tx_model_bytes']+=size
            stats['data_tx_packets' if proto==17 else 'control_tx_packets']+=1
        elif event==3:
            pending[packet_key].append(t)
            stats['max_enqueue_queue_bytes']=max(stats['max_enqueue_queue_bytes'],queue_bytes)
        elif event==4:
            if not pending[packet_key]:unmatched+=1;continue
            begin=pending[packet_key].popleft();wait=t-begin;assert wait>=0
            paired+=1;stats['matched_dequeues']+=1;stats['sum_residence_packet_ns']+=wait
            stats['max_residence_ns']=max(stats['max_residence_ns'],wait)
            if wait:
                witnesses[group].append(dict(node=node,port=port,peer=links[node,port],group=group,
                    packet_uid=uid,stream=stream,protocol=proto,sequence=seq,model_bytes=size,
                    enqueue_ns=begin,dequeue_ns=t,residence_ns=wait))
    port_rows=list(sorted(ports.values(),key=lambda p:(p['node'],p['port'],p['group'])))
    shared=[]
    for node,port in sorted({(p['node'],p['port']) for p in port_rows}):
        if all(ports.get((node,port,g),{}).get('tx_packets',0)>0 for g in (0,1)):
            shared.append(dict(node=node,port=port,peer=links[node,port],
                group_model_bytes=[ports[node,port,g]['tx_model_bytes'] for g in (0,1)]))
    assert not exposure[1], 'Second group used the bad physical edge; pure bystander interpretation rejected'
    return dict(capture_events=len(capture['events']),capture_complete=True,bad_edge_exposure=[dict(c) for c in exposure],
        unknown_stream_events=dict(unknown),captured_ports=port_rows,shared_captured_ports=shared,
        matched_queue_pairs=paired,unmatched_dequeues=unmatched,pending_enqueue_records=sum(map(len,pending.values())),
        longest_queue_witnesses=[sorted(w,key=lambda x:(-x['residence_ns'],x['enqueue_ns']))[:12] for w in witnesses],
        scope='Actual source/endpoints plus switches128/132/133 only; packet-ns sum is not task critical-path delay')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    study=a.study.resolve();root=study.parent;out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    frozen=load(study/'frozen.json');topology=load(study/'topology/topology.json');cases={};execution_hash=None
    for case in frozen['cases']:
        run=root/('run-'+case);audit=load(root/(case+'-audit.json'))
        assert audit['passed']
        actual=sha(run/'execution.json')
        if execution_hash is None:execution_hash=actual
        assert actual==execution_hash
        work=load(run/'execution.json');result=load(run/'result.json')
        assert result['passed'] and result['drained'] and result['gpu_count']==128
        groups=[]
        for i,(group,report,summary) in enumerate(zip(work['groups'],result['legacy_shared']['groups'],audit['groups'])):
            assert group['id']==report['id']==summary['id']
            detail=critical_predecessors(group['execution'],report['legacy'])
            assert detail['last_kernel_complete_ns']==summary['last_kernel_complete_ns']
            groups.append(dict(summary=summary,**detail))
        measured=observations(result,work,topology)
        record=dict(groups=groups,physical_finish_ns=result['finish_ns'],observations=measured,
            queue_metrics=[row for row in result['ports'] if 'queue_metrics' in row],
            reference_events=len(result['legacy_shared']['transport']['dcqcn_reference']['events']),
            result_bytes=(run/'result.json').stat().st_size,
            identity=dict(execution_sha256=actual,result_sha256=sha(run/'result.json'),audit_sha256=sha(root/(case+'-audit.json'))))
        cases[case]=record
        (out/(case+'-mechanism.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        del result,work,audit;gc.collect()
    comparisons=[]
    for group in (0,1):
        normal,local,prefix=[cases[c]['groups'][group]['last_kernel_complete_ns'] for c in frozen['cases']]
        comparisons.append(dict(group=group,normal_ns=normal,local_ns=local,prefix_ns=prefix,
            local_extra_ns=local-normal,prefix_extra_ns=prefix-normal,mature_repair_improvement_ns=local-prefix,
            mature_repair_improvement_fraction=(local-prefix)/local,
            recovered_local_extra_fraction=(local-prefix)/(local-normal) if local>normal else None))
        assert len({cases[c]['groups'][group]['summary']['final_memory_sha256'] for c in frozen['cases']})==1
    summary=dict(passed=True,network_endpoints=128,ep_sizes=[32,32],requests=[8,7],choices=120,
        comparisons=comparisons,cases={c:dict(task_ns=[g['last_kernel_complete_ns'] for g in x['groups']],
            physical_finish_ns=x['physical_finish_ns'],repeated_bytes=[g['summary']['retransmitted_payload_bytes'] for g in x['groups']],
            capture_events=x['observations']['capture_events'],bad_edge_exposure=x['observations']['bad_edge_exposure'],
            shared_captured_ports=x['observations']['shared_captured_ports'],identity=x['identity']) for c,x in cases.items()},
        frozen_sha256=sha(study/'frozen.json'),source_manifest_sha256=frozen['source_manifest_sha256'],
        algorithm_innovation_validated=False,full_inference_executed=False,hardware_calibrated=False,
        scope='Two complete functional legacy groups driven by15 real recorded selections; single deterministic case, not full-cluster load or modern RoCE performance')
    (out/'analysis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(comparisons))


if __name__=='__main__':main()
