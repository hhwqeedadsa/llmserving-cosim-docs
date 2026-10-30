"""Audit actual recovery scope and costs; never simulate a modified task."""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path

from dependencies import expanded,closure,path_to
from prepare import ROOT,files,load,save,sha

def csv_out(p,rows):
    assert rows
    with p.open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)

def source_status(prefix,timeout):
    """The only decision-side reader: source callbacks and the initial plan."""
    wave=prefix['graph'];messages={m['id']:m for m in wave['messages']};state={};sequence=-1;time=-1
    for e in prefix['observations']:
        assert e['schema_version']=='moe-execution-event/1' and e['type']=='OBSERVE' and e['graph_id']==wave['graph_id']
        assert e['kind'] in ('message_send_post','message_send_complete')
        assert e['sequence']>sequence and time<=e['time_ns']<=prefix['deadline_ns']
        assert e['message'] in messages and e['rank']==messages[e['message']]['src']
        sequence,time=e['sequence'],e['time_ns'];mid=e['message']
        if e['kind']=='message_send_post':
            assert mid not in state;state[mid]=dict(post_ns=time,ack_ns=None)
        else:assert mid in state and state[mid]['ack_ns'] is None;state[mid]['ack_ns']=time
    assert sequence<=prefix['observed_sequence']
    pending=sorted(mid for mid,m in state.items() if m['ack_ns'] is None)
    overdue=[mid for mid in pending if state[mid]['post_ns']+timeout<=prefix['deadline_ns']]
    return dict(posted=len(state),confirmed=len(state)-len(pending),pending=pending,overdue=overdue)

def reconstruct(path,group_index):
    c=load(path/'input.json');assert c['workload']['batch_per_rank']==1 and 'expert_to_group_rank' not in c['route_replay']
    group=c['parallel']['groups'][group_index];primary=c['parallel']['groups'][0];size=c['model']['experts']//len(group)
    assert len(group)==32 and size==8 and c['model']['top_k']==8
    manifest_path=path/c['route_replay']['manifest']['path'];assert sha(manifest_path)==c['route_replay']['manifest']['sha256']
    manifest=load(manifest_path);entries={q['file_id']:q for q in manifest['files']}
    requests={};raw={}
    for q in c['route_replay']['requests']:
        if q['rank_id'] not in primary:continue
        ref=entries[q['file_id']]['file'];source=path/ref['path'];assert sha(source)==ref['sha256']
        raw[q['rank_id']]=load(source)
        requests[q['rank_id']]=dict(primary_request=q['rank_id'],file_id=q['file_id'],source_sha256=ref['sha256'],
            network_payload_bytes=0,declared_compute_ns=0,bundles=0,expert_contributions=0,local_bundles=0)
    assert len(requests)==4
    waves=[load(path/'graphs'/f'g{group_index}.w{step}'/'wave.json') for step in range(c['workload']['decode_steps'])]
    bundles=[];expected_messages={};total_compute=0
    for wave in waves:
        expected=Counter();contributions=Counter();compute=Counter();bindings=defaultdict(list)
        messages={(m['layer'],m['phase'],m['src'],m['dst']):m for m in wave['messages']}
        assert len(messages)==len(wave['messages'])
        for layer in wave['routes']:
            lid=layer['layer'];assert layer['source_layer_id']==c['route_replay']['layer_ids'][lid]
            assert len(layer['tokens'])==4
            ds=wave['token_transport']['payload_bytes_per_token']['dispatch'];cs=wave['token_transport']['payload_bytes_per_token']['combine']
            assert ds==14464 and cs==14400
            for token in layer['tokens']:
                src=token['rank'];original=primary[group.index(src)];q=next(q for q in c['route_replay']['requests'] if q['rank_id']==original)
                choices=raw[original][q['first_decode_entry']+wave['decode_step']][str(layer['source_layer_id'])][0]
                assert choices==token['experts'] and token['slot']==0
                request=requests[original];local_work=c['compute']['attention_ns_per_token']+c['compute']['pack_ns_per_token']+c['compute']['merge_ns_per_token']
                request['declared_compute_ns']+=local_work
                for name,key in [('attention','attention_ns_per_token'),('pack','pack_ns_per_token'),('merge','merge_ns_per_token')]:
                    compute[src,lid,name]+=c['compute'][key]
                destinations=defaultdict(list)
                for expert in choices:destinations[group[expert//size]].append(expert)
                for dst,experts in sorted(destinations.items()):
                    extra=len(experts)-1;work=len(experts)*c['compute']['expert_ns_per_contribution']+extra*c['token_transport']['local_reduce_ns_per_extra']
                    request['declared_compute_ns']+=work;request['expert_contributions']+=len(experts);request['bundles']+=1
                    compute[dst,lid,'experts']+=len(experts)*c['compute']['expert_ns_per_contribution'];compute[dst,lid,'combine_reduce']+=extra*c['token_transport']['local_reduce_ns_per_extra']
                    dispatch=messages.get((lid,'dispatch',src,dst));combine=messages.get((lid,'combine',dst,src))
                    row=dict(graph=wave['graph_id'],step=wave['decode_step'],layer=lid,source_layer=layer['source_layer_id'],
                        primary_request=original,src=src,dst=dst,experts=experts,declared_expert_and_reduce_ns=work,
                        dispatch_message=None if dispatch is None else dispatch['id'],combine_message=None if combine is None else combine['id'],
                        network_payload_bytes=0 if dst==src else ds+cs)
                    bundles.append(row)
                    if dst==src:assert dispatch is combine is None;request['local_bundles']+=1;continue
                    assert dispatch is not None and combine is not None
                    for phase,s,d,bytes_ in [('dispatch',src,dst,ds),('combine',dst,src,cs)]:
                        key=lid,phase,s,d;expected[key]+=bytes_;contributions[key]+=len(experts);bindings[messages[key]['id']].append(row)
                    request['network_payload_bytes']+=ds+cs
        assert set(expected)==set(messages)
        for key,message in messages.items():
            assert message['bytes']==expected[key] and message['contributions']==contributions[key]
            assert message['transport_tokens']==len(bindings[message['id']])
            expected_messages[message['id']]=message
        for rank,tasks in wave['rank_nodes'].items():
            rank=int(rank)
            for task in tasks:
                if task['kind']!='COMP':continue
                layer,stage=task['name'].split('.',1);value=compute[rank,int(layer[1:]),stage]
                assert task['duration_ns']==value;total_compute+=value
    result=load(path/'result.json');native={m['id']:m for m in result['service_transport']['messages']}
    for mid,message in expected_messages.items():assert all(native[mid][key]==value for key,value in message.items())
    assert sum(q['network_payload_bytes'] for q in requests.values())==sum(m['bytes'] for m in expected_messages.values())
    assert sum(q['declared_compute_ns'] for q in requests.values())==total_compute
    return dict(requests=requests,bundles=bundles,waves=waves,network_payload_bytes=sum(m['bytes'] for m in expected_messages.values()),declared_compute_ns=total_compute)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--frozen',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();frozen=load(a.frozen)
    assert frozen['passed'] and sha(Path(__file__).with_name('protocol.md'))==frozen['method_sha256']
    for name,digest in frozen['analyzer_sources_sha256'].items():assert sha(ROOT/name)==digest
    for name,digest in frozen['source_sha256'].items():assert sha(ROOT/name)==digest
    for record in frozen['records'].values():assert files(Path(record['path']))==record['sha256']
    paths={name:Path(v['path']) for name,v in frozen['records'].items()}
    status={};prefixes={}
    for condition,record in frozen['prefixes'].items():
        path=Path(record['path']);assert sha(path)==record['sha256'];prefixes[condition]=load(path)
        status[condition]=source_status(prefixes[condition],18000)
    primary=reconstruct(paths['guarded-local'],0);backup=reconstruct(paths['guarded-local'],1)
    assert primary['requests']==backup['requests']
    nodes,parents,children=expanded(primary['waves']);overdue=set(status['local']['overdue'])
    roots={kind:{key for key,node in nodes.items() if key[0]=='g0.w0' and node['kind']==kind and node['message']['id'] in overdue} for kind in ('SEND','RECV')}
    assert len(roots['SEND'])==len(roots['RECV'])==len(overdue)
    reach={kind:closure(keys,children) for kind,keys in roots.items()};reach['combined']=closure(set.union(*roots.values()),children)
    missing=[b for b in primary['bundles'] if b['graph']=='g0.w0' and b['dispatch_message'] in overdue]
    direct={b['primary_request'] for b in missing};assert len(missing)==len(overdue)
    observed=[json.loads(line) for line in (paths['guarded-local']/'execution_events.jsonl').read_text().splitlines()]
    callbacks={(e.get('graph_id'),e.get('message'),e['kind']):e['time_ns'] for e in observed if e['kind'].startswith('message_')}
    times={(e.get('graph_id'),e.get('rank'),e.get('node'),e['kind']):e['time_ns'] for e in observed if e['kind'].startswith('node_')}
    deadline=frozen['deadline_ns'];before=[e for e in observed if e['sequence']<=prefixes['local']['observed_sequence']]
    complete={ (e.get('graph_id'),e.get('rank'),e.get('node')) for e in before if e['kind']=='node_complete'}
    issued={ (e.get('graph_id'),e.get('rank'),e.get('node')) for e in before if e['kind']=='node_issue'}
    first_experts={k for k,n in nodes.items() if k[0]=='g0.w0' and n['name']=='L0.experts'}
    first_dispatch=next(g for g in load(paths['guarded-local']/'result.json')['service']['phase_completion']['gates'] if g['graph_id']=='g0.w0' and g['id']=='L0.dispatch')
    requests=[];proofs=[]
    for identity,q in primary['requests'].items():
        final=next(k for k,n in nodes.items() if k[:2]==('g0.w1',identity) and n['name']=='L1.merge')
        witness=path_to(set.union(*roots.values()),final,children);assert witness is not None
        row=dict(q,directly_has_overdue_input=identity in direct,blocked_by_send_dependency=final in reach['SEND'],
            blocked_by_recv_dependency=final in reach['RECV'],current_primary_last_merge_ns=times[final+('node_complete',)])
        initial=[b for b in primary['bundles'] if b['graph']=='g0.w0' and b['layer']==0 and b['primary_request']==identity and b['dispatch_message'] is not None]
        row['first_remote_inputs']=len(initial);row['first_remote_inputs_received_by_trigger']=sum(callbacks['g0.w0',b['dispatch_message'],'message_recv_complete']<=deadline for b in initial)
        requests.append(row);proofs.append(dict(primary_request=identity,path=[dict(graph=k[0],rank=k[1],node=k[2],name=nodes[k]['name'],kind=nodes[k]['kind']) for k in witness]))
    first_messages=[m for m in primary['waves'][0]['messages'] if m['layer']==0 and m['phase']=='dispatch']
    missing_eval=[]
    for bundle in missing:
        mid=bundle['dispatch_message'];missing_eval.append(dict(bundle,source_post_ns=callbacks['g0.w0',mid,'message_send_post'],
            actual_input_receive_ns=callbacks['g0.w0',mid,'message_recv_complete'],actual_source_ack_ns=callbacks['g0.w0',mid,'message_send_complete']))
    ready_first=[m for m in first_messages if callbacks['g0.w0',m['id'],'message_recv_complete']<=deadline]
    unchanged={name:files(path)==frozen['records'][name]['sha256'] for name,path in paths.items()};assert all(unchanged.values())
    result=dict(passed=True,source_only_status=status,trigger_ns=deadline,missing_input_bundles=missing_eval,
        direct_request_count=len(direct),target_request_count=len(requests),dependency_paths=proofs,per_request=requests,
        closure_counts={name:dict(nodes=len(keys),first_layer_expert_nodes=len(keys&first_experts),
            target_final_merges=sum(any(k in keys and k[:2]==('g0.w1',q) and nodes[k]['name']=='L1.merge' for k in nodes) for q in primary['requests'])) for name,keys in reach.items()},
        first_stage=dict(remote_dispatch_messages=len(first_messages),received_messages=len(ready_first),
            received_payload_bytes=sum(m['bytes'] for m in ready_first),unreceived_payload_bytes=sum(m['bytes'] for m in first_messages if m not in ready_first),
            first_expert_nodes=len(first_experts),first_expert_issued_before_trigger=len(first_experts&issued),first_expert_completed_before_trigger=len(first_experts&complete),
            declared_compute_completed_before_trigger_ns=sum(nodes[k]['duration_ns'] for k in complete if k in nodes and k[0]=='g0.w0' and nodes[k]['kind']=='COMP'),
            dispatch_members_arrived_before_trigger=sum(m['arrived_ns']<=deadline for m in first_dispatch['members']),dispatch_release_ns=first_dispatch['released_ns']),
        complete_backup=dict(network_payload_bytes=backup['network_payload_bytes'],declared_compute_ns=backup['declared_compute_ns'],
            all_rank_bundles=len(backup['bundles']),expert_contributions=sum(len(b['experts']) for b in backup['bundles'])),
        subset_accounting_only=dict(selected_requests=sorted(direct),excluded_requests=sorted(set(primary['requests'])-direct),
            selected_network_bytes=sum(q['network_payload_bytes'] for q in requests if q['primary_request'] in direct),
            selected_declared_compute_ns=sum(q['declared_compute_ns'] for q in requests if q['primary_request'] in direct),
            existing_consumer_accepts_subset=False,equivalent_task_or_speedup_proven=False),
        existing_records_unchanged=unchanged,new_network_executions=0,analyzer_sha256=sha(Path(__file__)),
        dependency_analyzer_sha256=sha(Path(__file__).with_name('dependencies.py')),
        scope='Structural reachability and linear declared work attribution only. Source predicate never sees receiver truth. No modified execution, counterfactual latency, numerical tensor equivalence, full671B or new recovery benefit.')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);save(out/'analysis.json',result)
    csv_out(out/'per-request.csv',requests)
    csv_out(out/'missing-bundles.csv',[dict(**{k:v for k,v in b.items() if k!='experts'},experts=' '.join(map(str,b['experts']))) for b in missing_eval])
    print(json.dumps(dict(passed=True,source_status=status,first_stage=result['first_stage'],backup=result['complete_backup'],subset=result['subset_accounting_only'])))

if __name__=='__main__':main()
