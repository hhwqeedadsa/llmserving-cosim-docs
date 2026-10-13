"""Internal C1 review of equal-resource FIFO and producer round robin.

Uses the complete consumer gate analysis, all actual STORE request ranges and
selected endpoint queue states. Packet feedback remains part of the total effect.
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path
import sys

from moe_ub_cosim.native_queue import checked_rows, checked_requests
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from .consumer_review import completion_conditions
from .path_replay import row_digest
from .workflow import digest, host, read, save


def fixed_effective(value,mode):
    """Object readback includes every native initiator, not only captured ranks."""
    from copy import deepcopy
    from .organization import CONTROL
    control=read(CONTROL);value=deepcopy(value);key=control['attribute']
    if value['object_validation']!='matched_before_business_activation':raise ValueError('Missing object validation')
    if value['attributes'].pop(key)!=mode:raise ValueError('Effective queue policy differs')
    if any(value['attributes'].get(k)!=v for k,v in control['fixed_attributes'].items()):
        raise ValueError('Effective shared budget differs')
    for name in ('origins','catalog'):value.pop(name)
    threads=[];instances=[];apis=[]
    for obj in value['objects']:
        attrs=obj['attributes'];role=obj['role']
        if role.startswith('native_thread:'):
            if role!='native_thread:0' or attrs.pop(key)!=mode:raise ValueError('Native thread policy/count differs')
            threads.append(obj['node_id'])
        if role=='native_instance':instances.append(obj['node_id'])
        if role=='native_api':apis.append(obj['node_id'])
        for name,expected in control['fixed_attributes'].items():
            if name in attrs and attrs[name]!=expected:raise ValueError('Effective native object budget differs')
    if not threads or sorted(threads)!=sorted(instances) or sorted(threads)!=sorted(apis) or len(set(threads))!=len(threads):
        raise ValueError('Native object coverage differs')
    return dict(fixed_digest=row_digest([value]),native_initiators=len(threads),one_worker_per_initiator=True)


def source_chain(plan,times,send,expert):
    """Keep every tied latest branch; one telescoping path reports elapsed time."""
    pending=list(plan[send]['deps']);seen={};boundaries=[]
    ready=max((times[d]['end_ns'] for d in pending),default=0)
    if times[send]['start_ns']!=ready:raise ValueError('Unexplained SEND scheduling gap')
    roots=sorted(d for d in pending if times[d]['end_ns']==ready);pending=list(roots)
    while pending:
        nid=pending.pop()
        if nid in seen:continue
        n,t=plan[nid],times[nid]
        if nid==expert:
            seen[nid]=dict(node_id=nid,boundary='source routed expert end',end_ns=t['end_ns']);continue
        deps=n['deps'];at=max((times[d]['end_ns'] for d in deps),default=0)
        ties=sorted(d for d in deps if times[d]['end_ns']==at)
        kind='result_pack' if n['name']=='combine-result-pack' else n.get('event',n['kind'])
        row=dict(node_id=nid,name=n['name'],kind=kind,start_ns=t['start_ns'],end_ns=t['end_ns'],
            duration_ns=t['end_ns']-t['start_ns'],ready_to_start_ns=t['start_ns']-at,
            latest_dependency_ties=ties,transfer_id=n.get('transfer_id'))
        seen[nid]=row
        if not deps or n.get('layer_id')!=plan[send].get('layer_id'):
            row['boundary']='prior-layer or dependency-free node';boundaries.append(nid)
        elif t['start_ns']!=at:
            # This experiment must not silently charge resource scheduling to the network.
            raise ValueError('Source chain has a scheduling gap; inspect exclusive resource owners')
        else:pending.extend(ties)
        if len(seen)>10000:raise ValueError('Source chain witness exceeds bound')
    components=Counter();path=[];current=roots[0]
    while current!=expert:
        row=seen[current]
        if 'boundary' in row:break
        path.append(current);components[row['kind']]+=row['duration_ns'];current=row['latest_dependency_ties'][0]
    complete=current==expert
    elapsed=times[send]['start_ns']-times[expert]['end_ns']
    if complete and sum(components.values())!=elapsed:raise ValueError('Source chain does not telescope')
    return dict(source_expert_end_ns=times[expert]['end_ns'],submit_ns=times[send]['start_ns'],
        expert_to_submit_ns=elapsed,submit_dependency_ties=roots,all_latest_dependency_dag=list(seen.values()),
        representative_path=path,representative_components_ns=dict(components),path_reaches_expert=complete,
        other_boundaries=boundaries,scope='All ties retained; components describe one equal-length elapsed path, not a sum across branches')


def queue_review(execution,report,targets):
    """Actual next selectable producer, never treat RR flattening as FIFO order."""
    selected=set(report['native_queue_capture']['policy']['rank_ids'])
    targets={tid:dict(transfer_id=tid,rank_id=t['src_rank'],submitted_ns=t['send_start_ns'],
        intervals=[],formed_ns=None,last=None) for tid,t in targets.items() if t['src_rank'] in selected}
    event_counts=Counter();peaks=Counter()
    for row in checked_rows(execution,report):
        event_counts[row['event']]+=1;peaks[row['rank_id']]=max(peaks[row['rank_id']],len(row['pending']))
        if row['store_capacity']!=32 or row['request_pool_capacity']!=256 or row['thread_id']!=0:
            raise ValueError('Queue capture changed shared resources')
        if row['event']=='final' and (row['pending'] or row['queue']):raise ValueError('Undrained selected worker')
        for tid,target in targets.items():
            if row['rank_id']!=target['rank_id'] or target['formed_ns'] is not None:continue
            affected=row['affected'];matches=affected and affected['identity']['transfer_id']==tid
            if matches and row['event']=='task_queued':
                if row['time_ns']!=target['submitted_ns']:raise ValueError('Task submit/queue mismatch')
                target['producer']=affected['identity']['producer'];target['task_id']=affected['task_id']
                target['enqueue']=dict(sequence=row['sequence'],time_ns=row['time_ns'],policy=row['store_queue_policy'],
                    last_store_producer=row['last_store_producer'],inflight=row['store_inflight'],pool_used=len(row['pending']),
                    other_queued_tasks=[dict(task_id=q['task_id'],identity=q['identity'],remaining_bytes=q['remaining_bytes'])
                        for q in row['queue'] if q['task_id']!=target['task_id']],
                    pending_owners=row['pending'])
            if 'enqueue' not in target:continue
            old=target['last']
            if old and old['time_ns']<row['time_ns']:
                queue=[q for q in old['queue'] if q['operation']=='STORE']
                if not queue or not any(q['task_id']==target['task_id'] for q in queue):
                    raise ValueError('Waiting status missing from actual queue')
                head=queue[0];reason=('worker_full' if old['store_inflight']==32 else
                    'pool_full' if len(old['pending'])==256 else 'capacity_available')
                target['intervals'].append(dict(start_ns=old['time_ns'],end_ns=row['time_ns'],reason=reason,
                    next_is_target=head['task_id']==target['task_id'],next_identity=head['identity'],
                    inflight=old['store_inflight'],pool_used=len(old['pending'])))
            if matches and row['event']=='request_issued':
                if row['affected']['offset']!=0 or row['affected']['bytes']!=32:raise ValueError('Status was resegmented')
                target['formed_ns']=row['time_ns'];target['formed_sequence']=row['sequence']
            target['last']=row
    rows=[]
    for tid,t in sorted(targets.items()):
        t.pop('last')
        if t['formed_ns'] is None:raise ValueError('Selected status formation not captured')
        t['wait_ns']=t['formed_ns']-t['submitted_ns'];durations=Counter();next_core=Counter()
        for interval in t['intervals']:
            dt=interval['end_ns']-interval['start_ns'];durations[interval['reason']]+=dt
            next_core[interval['next_identity']['producer']]+=dt
        if sum(durations.values())!=t['wait_ns']:raise ValueError('Uncovered status wait interval')
        t.update(state_duration_ns=dict(durations),next_producer_duration_ns=dict(next_core),
            next_is_target_ns=sum(x['end_ns']-x['start_ns'] for x in t['intervals'] if x['next_is_target']))
        rows.append(t)
    return dict(event_counts=dict(event_counts),peak_pending_by_rank=dict(peaks),statuses=rows,
        scope='Selected endpoints only. RR list groups each producer FIFO starting at next eligible producer; other queued tasks are not a strict predecessor sequence.')


def review(case,output):
    case,output=Path(case),Path(output);run=case/'run';execution=run/'raw/execution'
    if output.exists():raise ValueError('Review output must be new')
    selection=read(case/'selection.json');bundle=Path(selection['slice']);report=read(execution/'result.json')
    if not report['normal_exit'] or not report['network_drain']['idle']:raise ValueError('Unfinished execution')
    effective=fixed_effective(read(execution/'effective_backend.json'),selection['mode'])
    plans={};models={};experts={};nodes=[]
    for path in sorted((bundle/'raw/graphs').glob('*/plan.json')):
        p=read(path);gid=p['graph_id']
        for graph in p['rank_graphs']:
            key=gid,graph['rank_id'];plans[key]={n['node_id']:n for n in graph['nodes']};models[key]=graph['model_stages']
            for phase in graph['layer_phases']:experts[key+(phase['layer_id'],)]=phase['phase_nodes']['expert']
        del p
    times=defaultdict(dict)
    for row in iter_result(execution,'node_execution',report):times[row['graph_id'],row['rank_id']][row['node_id']]=row
    for key,plan in plans.items():
        if set(plan)!=set(times[key]):raise ValueError('Executed graph coverage changed')
        for nid,n in plan.items():
            if n['kind']=='COMP':
                t=times[key][nid]
                if t['end_ns']-t['start_ns']!=n['duration_ns']:raise ValueError('Compute cost changed')
                nodes.append(dict(graph_id=key[0],rank_id=key[1],node_id=nid,duration_ns=n['duration_ns'],resource_id=n['resource_id']))
    planned={t['transfer_id']:t for t in iter_result(execution,'planned_transfers',report)}
    actual={t['transfer_id']:t for field in ('transfers','local_deliveries') for t in iter_result(execution,field,report)}
    if set(planned)!=set(actual):raise ValueError('Incomplete transfer coverage')
    requests=defaultdict(list);first={};last={};producer={};identities={}
    for row in checked_requests(execution,report):
        i=row['identity'];tid=i['transfer_id'];requests[tid].append((row['offset'],row['bytes']))
        if tid in identities and identities[tid]!=i:raise ValueError('Request producer or task identity changed within transfer')
        first.setdefault(tid,row['time_ns']);last[tid]=row['time_ns'];producer[tid]=i['producer'];identities[tid]=i
    canonical=[dict(identity=identities[tid],ranges=sorted(parts)) for tid,parts in sorted(requests.items())]
    request_summary=row_digest(canonical);request_summary.update(requests=sum(map(len,requests.values())),all_native_STORE=True)
    del canonical
    endpoints={(r['rank_id'],r['local_record_id']):r for r in iter_result(execution,'endpoint_ops',report)}
    consumers=[];latest=[];contributions={}
    for op in iter_result(execution,'operation_registry',report):
        if op['kind']!='combine' or op['layer_id'] not in (7,8):continue
        lid=op['layer_id'];members={m['rank_id']:endpoints[m['rank_id'],m['local_record_id']] for m in op['endpoint_records']}
        end=max(r['end_local_ns'] for r in members.values());ranks=sorted(r for r,v in members.items() if v['end_local_ns']==end)
        latest.append(dict(layer_id=lid,combine_end_ns=end,ranks=ranks))
        for rank in sorted(set(ranks)|{{7:27,8:58}[lid]}):
            key=next(k for k in plans if k[1]==rank);p,t=plans[key],times[key]
            finish=next(nid for nid,n in p.items() if n.get('layer_id')==lid and n['name']=='combine-operator-complete')
            conditions=completion_conditions(p,t,planned,actual,finish,lid)
            if conditions['finish']['end_ns']!=members[rank]['end_local_ns']:raise ValueError('Endpoint/node completion mismatch')
            consumers.append(dict(layer_id=lid,rank_id=rank,is_actual_last=rank in ranks,completion=conditions,
                following_compute=[dict(layer_id=s['layer_id'],stage=s['stage'],**t[s['node_id']]) for s in models[key]
                    if s['node_id'] is not None and (s['layer_id'],s['stage']) in
                    [(lid,'shared_routed_add'),(lid,'ffn_residual'),(lid+1,'attention_norm'),(lid+1,'router')]]))
            for n in conditions['latest_completion_dag']:
                dc=n.get('delivery_condition')
                if dc and dc['pending_after_start']:
                    tid=dc['identity']['transfer_id'];contributions.setdefault(tid,set()).add('consumer '+str(rank)+' critical gate')
    initial=read(Path(__file__).parent/'results/e2/c1-initial-review.json')
    for c in initial['chains']:contributions.setdefault(c['key_transfer_id'],set()).add('original key data')
    sources=[];status_ids={s['transfer_id'] for c in initial['chains'] for s in c['steps'] if s['kind']=='local_read_complete'}
    for tid,reasons in sorted(contributions.items()):
        transfer=planned[tid];a=actual[tid]
        if 'ub_task_id' not in transfer:continue
        key=next(k for k in plans if k[1]==transfer['src_rank']);p,t=plans[key],times[key]
        send=next(nid for nid,n in p.items() if n['kind']=='SEND' and n['transfer_id']==tid)
        lid=p[send]['layer_id'];chain=source_chain(p,t,send,experts[key+(lid,)])
        for n in chain['all_latest_dependency_dag']:
            sid=n.get('transfer_id')
            if sid and planned[sid]['message_role']=='status' and planned[sid]['payload_bytes']==32:status_ids.add(sid)
        sources.append(dict(transfer_id=tid,layer_id=lid,src_rank=transfer['src_rank'],dst_rank=transfer['dst_rank'],
            message_role=transfer['message_role'],reasons=sorted(reasons),producer=producer[tid],
            first_request_ns=first[tid],last_request_ns=last[tid],request_ranges=sorted(requests[tid]),
            data_ready_ns=a['data_ready_ns'],transaction_done_ns=a['transaction_done_ns'],chain=chain))
    queue=queue_review(execution,report,{tid:actual[tid] for tid in status_ids})
    selected={t['transfer_id'] for t in queue['statuses']}
    uncaptured=[dict(transfer_id=tid,source_rank=planned[tid]['src_rank'],submit_ns=actual[tid]['send_start_ns'],
        first_request_ns=first[tid],last_request_ns=last[tid],limitation='full queue state not captured at this source')
        for tid in sorted(status_ids-selected)]
    compute=[]
    for key,stages in models.items():
        compute.extend(dict(stage=s['stage'],layer_id=s['layer_id'],**times[key][s['node_id']])
            for s in stages if s['node_id'] is not None)
    ports=defaultdict(Counter)
    for p in read(run/'observed/public_config.json')['ports']:ports[p['switch_id'],p['port_id']]
    for r in stored_rows(run/'observed','port_windows.csv'):
        ports[r['switch_id'],r['port_id']].update(packets=r['counted_packets'],packet_bytes=r['counted_packet_bytes'])
    for r in stored_rows(run/'truth','message_attribution_windows.csv'):
        ports[r['switch_id'],r['port_id']][r['message_category']]+=r['application_bytes']
    save(case/'organization-ports.json',[dict(switch_id=k[0],port_id=k[1],**v) for k,v in sorted(ports.items())])
    save(case/'organization-queue.json',queue)
    compact_queue=[]
    for t in queue['statuses']:
        compact_queue.append({k:v for k,v in t.items() if k not in ('enqueue','intervals')})
        compact_queue[-1]['enqueue']={k:v for k,v in t['enqueue'].items() if k not in ('other_queued_tasks','pending_owners')}
        compact_queue[-1]['enqueue'].update(other_tasks=len(t['enqueue']['other_queued_tasks']),
            other_producers=sorted({q['identity']['producer'] for q in t['enqueue']['other_queued_tasks']}))
    save(output,dict(schema_version='issue-screening-organization-review/1',passed=True,mode=selection['mode'],
        manifest_sha256=digest(run/'manifest.json'),selection=selection,program_sha256=digest(__file__),
        effective_readback=effective,
        plan_files={str(p.relative_to(bundle)):digest(p) for p in (bundle/'raw/graphs').glob('*/plan.json')},
        executed_nodes=sum(map(len,times.values())),compute_cost_digest=row_digest(nodes),request_range_digest=request_summary,
        planned_transfer_digest=row_digest(iter_result(execution,'planned_transfers',report)),
        bytes_by_role=dict(Counter({role:sum(t['payload_bytes'] for t in planned.values() if t['message_role']==role)
                                   for role in {t['message_role'] for t in planned.values()}})),
        last_model_compute_end_ns=max(t['end_ns'] for t in compute if t['layer_id']==8),
        model_layer_end_ns={str(lid):max(t['end_ns'] for t in compute if t['layer_id']==lid) for lid in (6,7,8)},
        simulation_end_ns=report['simulation_end_ns'],service_finished_ns=report['service_finished_ns'],
        random=report['random'],network_drain=report['network_drain'],native_capture=report['native_queue_capture'],
        actual_last_consumers=latest,consumer_completion=consumers,contributions=sources,
        selected_status_waits=compact_queue,uncaptured_status_queues=uncaptured,
        queue_detail_sha256=digest(case/'organization-queue.json'),ports_sha256=digest(case/'organization-ports.json'),
        scope='Fixed graph and shared resource organization total effect; packet feedback is not independently separated'))
    print(dict(passed=True,last_model_compute_end_ns=max(t['end_ns'] for t in compute if t['layer_id']==8),latest=latest))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:review(a.case,a.output)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-resources',**host())
        if r['status']!='completed':raise SystemExit(r['status'])


if __name__=='__main__':main()
