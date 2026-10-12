"""C1 selected-endpoint capture and queue-owner review through public interfaces.

This is internal E2 mechanism evidence. Original baseline guards remain intact;
each capture explicitly binds a separately accepted platform and backend.
"""
import argparse
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import shutil
import gzip
import hashlib
import io
import json
import tarfile

from moe_ub_cosim.native_queue import checked_rows
from moe_ub_cosim.record_storage import iter_result
from moe_ub_cosim.research_storage import stored_rows
from .workflow import ROOT, CONFIG, read, save, digest, host, cli


def prepare(case, output, backend, platform, quota=32):
    case,output,backend=map(lambda p:Path(p).resolve(),(case,output,backend))
    if subprocess.run(['git','diff','--quiet',platform,'--','python/moe_ub_cosim','backend','deps.lock.json',
                       'third_party/LLMServingSim','third_party/ns-3-ub'],cwd=ROOT).returncode:
        raise ValueError('Native diagnostic platform differs from explicitly selected commit')
    output.mkdir(parents=True,exist_ok=False)
    cli(output,'original-validate','validate','--run',case/'run')
    cli(output,'frozen-validate','slice','inspect','--slice',case/'slice','--verify')
    observation=read(case/'run/inputs/observation.json')
    observation.update(schema_version='moe-ub-observation-config/5',native_queue=dict(
        schema_version='moe-ub-native-queue-config/1',rank_ids=[70,65],end_ns=120000,
        max_records=100000,max_snapshot_items=4096))
    save(output/'observation.json',observation)
    original=read(case/'run/inputs/effective.json')
    config=dict(schema_version='moe-ub-replay/2',host=host(True,[case,output]),
        export_host=host(extra_roots=[case,output]),backend=str(backend),
        backend_timeout_seconds=original['backend_timeout_seconds'],max_simulation_ns=original['max_simulation_ns'],
        observation=str(output/'observation.json'),export_policy=str(CONFIG/'export-policy.json'))
    control=None
    if quota!=32:
        control=read(CONFIG/'controls/native-quota.json')
        assert quota==control['after']==64
        shutil.copytree(case/'slice/inputs/network',output/'network')
        settings=read(output/'network/backend.json')
        for k,v in control['fixed_attributes'].items():assert settings['attributes'][k]==v
        assert settings['attributes'][control['attribute']]==str(control['before'])
        settings['attributes'][control['attribute']]=str(quota)
        save(output/'network/backend.json',settings);config['network']=str(output/'network')
    save(output/'replay.json',config)
    save(output/'selection.json',dict(schema_version='issue-screening-native-selection/1',
        original_manifest_sha256=digest(case/'run/manifest.json'),platform_commit=platform,
        backend_sha256=digest(backend),policy=observation['native_queue'],
        program_sha256=digest(__file__),control=control,purpose='internal mechanism verification; not E3 input'))
    save(output/'case.json',dict(schema_version='issue-screening-case/1',owned_by='scaleup_issue_screening',
        case_id='c1-native-quota-'+str(quota),platform_commit=platform,backend_sha256=digest(backend)))


def identities(case):
    """Cores come from actual SEND dependencies, never time proximity."""
    result={}
    for path in sorted((Path(case)/'run/raw/graphs').glob('*/plan.json')):
        plan=read(path)
        for graph in plan['rank_graphs']:
            if graph['rank_id'] not in (70,65):continue
            nodes={n['node_id']:n for n in graph['nodes']}
            @lru_cache(None)
            def nearest(node):
                n=nodes[node]
                if n['kind']=='COMP':
                    return (n.get('resource_id',''),)
                return tuple(sorted({core for d in n['deps'] for core in nearest(d)}))
            for n in nodes.values():
                if n['kind']=='SEND':
                    result[plan['graph_id'],graph['rank_id'],n['node_id']]=list(nearest(n['node_id']))
        del plan
    return result


def compact_review(path):
    """Keep small reproducible findings in Git; full owner timelines in the case."""
    value=read(path);rows=[]
    for t in value['statuses']:
        head_ns=sum(x['end_ns']-x['start_ns'] for x in t['intervals'] if x['head_is_target'])
        by_core=Counter()
        for x in t['intervals']:
            by_core[','.join(x['head']['cores'])]+=x['end_ns']-x['start_ns']
        before=t['queued_state'];after=t['formed_state']
        rows.append(dict(**{k:t[k] for k in ('transfer_id','rank_id','submitted_ns','formed_ns','wait_ns',
                                             'nonoverlapping_state_ns','blocks')},
            queue_trace_sequences=[before['sequence'],after['sequence']],
            initial_worker_inflight=before['store_inflight'],worker_capacity=before['store_capacity'],
            initial_pool_used=len(t['queued_pending_owners']),pool_capacity=before['request_pool_capacity'],
            preceding_tasks=len(t['queued_predecessors']),
            preceding_cores=sorted({c for o in t['queued_predecessors'] for c in o['cores']}),
            own_head_wait_ns=head_ns,behind_other_tasks_ns=t['wait_ns']-head_ns,
            head_cores_time_ns=dict(by_core),resource_releases=len(t['releases']),
            first_release=t['releases'][0] if t['releases'] else None,
            last_release=t['releases'][-1] if t['releases'] else None))
    return dict(**{k:v for k,v in value.items() if k!='statuses'},statuses=rows,
                detailed_report=dict(member='queue-review.json',sha256=digest(path)),
                note='All original queue states, byte-range owners and releases remain in the detailed archived report and native_queue stream.')


def analyze(case, output):
    output=Path(output);execution=output/'run/raw/execution'
    report=read(execution/'result.json');core=identities(case)
    initial=read(Path(__file__).parent/'results/e2/c1-initial-review.json')
    tids={s['transfer_id'] for t in initial['chains'] for s in t['steps'] if s['kind']=='local_read_complete'}
    planned={t['transfer_id']:t for t in iter_result(execution,'planned_transfers') if t['transfer_id'] in tids}
    actual={t['transfer_id']:t for t in iter_result(execution,'transfers') if t['transfer_id'] in tids}
    def owner(task):
        i=task['identity'];key=(i['graph_id'],i['src_rank'],i['send_node_id'])
        return dict(**i,task_id=task['task_id'],thread_id=task['thread_id'],cores=core[key],
                    offset=task.get('offset'),bytes=task.get('bytes'),operation=task['operation'])
    targets={tid:dict(transfer_id=tid,rank_id=actual[tid]['src_rank'],submitted_ns=actual[tid]['send_start_ns'],
        task_id=None,formed_ns=None,last=None,intervals=[],releases=[],blocks=Counter()) for tid in tids}
    counts=Counter();maximum=Counter()
    for row in checked_rows(execution,report):
        counts[row['event']]+=1
        maximum[row['rank_id']]=max(maximum[row['rank_id']],len(row['pending']))
        for tid,t in targets.items():
            if row['rank_id']!=t['rank_id'] or t['formed_ns'] is not None:continue
            affected=row['affected'];is_target=affected and affected['identity']['transfer_id']==tid
            if is_target and row['event']=='task_queued':
                assert t['task_id'] is None and row['time_ns']==t['submitted_ns']
                t['task_id']=affected['task_id'];t['queued_state']=row
            if t['task_id'] is None:continue
            old=t['last']
            if old is not None and old['time_ns']<row['time_ns']:
                queue=[s for s in old['queue'] if s['operation']=='STORE']
                assert queue and any(s['task_id']==t['task_id'] for s in queue)
                head=queue[0];reason=('worker_quota_full' if old['store_inflight']==old['store_capacity'] else
                    'request_pool_full' if len(old['pending'])==old['request_pool_capacity'] else 'capacity_available')
                t['intervals'].append(dict(start_ns=old['time_ns'],end_ns=row['time_ns'],reason=reason,
                    head_is_target=head['task_id']==t['task_id'],head=owner(head),
                    head_remaining_bytes=head['remaining_bytes'],inflight=old['store_inflight'],
                    pool_used=len(old['pending']),pool_capacity=old['request_pool_capacity']))
            if row['event']=='response_released':
                t['releases'].append(dict(time_ns=row['time_ns'],owner=owner(affected),
                    remaining_inflight=row['store_inflight']))
            if row['event'] in ('outstanding_blocked','request_pool_blocked'):t['blocks'][row['event']]+=1
            if is_target and row['event']=='request_issued':
                t['formed_ns']=row['time_ns'];t['formed_state']=row
            t['last']=row
    rows=[]
    for tid,t in sorted(targets.items()):
        assert t['formed_ns'] is not None and planned[tid]['payload_bytes']==32
        t.pop('last');t['blocks']=dict(t['blocks']);durations=Counter()
        for x in t['intervals']:durations[x['reason']]+=x['end_ns']-x['start_ns']
        t['wait_ns']=t['formed_ns']-t['submitted_ns']
        assert sum(durations.values())==t['wait_ns']
        t['nonoverlapping_state_ns']=dict(durations)
        t['queued_pending_owners']=[owner(p) for p in t['queued_state']['pending']]
        t['queued_predecessors']=[owner(p) for p in t['queued_state']['queue'] if p['task_id']!=t['task_id']]
        t['queued_state'].pop('pending');t['queued_state'].pop('queue')
        t['formed_state'].pop('pending');t['formed_state'].pop('queue')
        rows.append(t)
    save(output/'queue-review.json',dict(schema_version='issue-screening-native-review/1',passed=True,
        run_manifest_sha256=digest(output/'run/manifest.json'),selection=read(output/'selection.json'),
        capture=report['native_queue_capture'],event_counts=dict(counts),peak_pending_by_rank=dict(maximum),
        statuses=rows,scope='Direct branch, queue, request owner and response evidence; not hardware calibration',
        program_sha256=digest(__file__)))
    save(output/'queue-summary.json',compact_review(output/'queue-review.json'))
    print({t['transfer_id']:t['nonoverlapping_state_ns'] for t in rows})


def predecessor_chain(plan, actual, end_node, start_node):
    """Follow this execution's latest dependency, retaining all ties.

    A different data chunk may satisfy a fence last in the control. Reusing
    the baseline's selected EVENT would omit that change in completion order.
    """
    steps=[];current=end_node;seen=set()
    while current!=start_node:
        assert current not in seen
        seen.add(current);n=plan[current];r=actual[current]
        assert n['deps'], 'Chain reached an unexpected boundary'
        latest=max(actual[d]['end_ns'] for d in n['deps'])
        ties=sorted(d for d in n['deps'] if actual[d]['end_ns']==latest)
        assert r['start_ns']==latest, 'Unexplained scheduling gap in selected chain'
        assert r['end_ns']>=r['start_ns']
        kind='result_pack' if n['name']=='combine-result-pack' else n.get('event',n['kind'])
        steps.append(dict(node_id=current,kind=kind,start_ns=r['start_ns'],end_ns=r['end_ns'],
                          duration_ns=r['end_ns']-r['start_ns'],transfer_id=n.get('transfer_id'),
                          latest_dependency_ties=ties))
        current=ties[0]
    steps.reverse();totals=Counter()
    for s in steps:
        if s['duration_ns']:totals[s['kind']]+=s['duration_ns']
    assert sum(totals.values())==actual[end_node]['end_ns']-actual[start_node]['end_ns']
    return steps,dict(totals)


def effect(case, output, baseline_capture):
    """Compare fixed contributions, all consumers and downstream computation."""
    from .analyze import compute_index
    from .path_replay import row_digest
    case,output=Path(case),Path(output);left=case/'run';right=output/'run'
    selection=read(output/'selection.json');assert selection['control']['after']==64
    cli(output,'validate-control','validate','--run',right)
    index=compute_index(left);initial=read(Path(__file__).parent/'results/e2/c1-initial-review.json')
    ids={t['key_transfer_id'] for t in initial['chains']}
    plans={}
    for path in sorted((left/'raw/graphs').glob('*/plan.json')):
        plan=read(path)
        for graph in plan['rank_graphs']:
            if graph['rank_id'] in (70,65):
                plans[plan['graph_id'],graph['rank_id']]={n['node_id']:n for n in graph['nodes']}
        del plan
    def describe(run):
        ex=run/'raw/execution';report=read(ex/'result.json');compute={};selected={}
        for n in iter_result(ex,'node_execution',report):
            k=(n['graph_id'],n['rank_id'],n['node_id'])
            if k in index:
                i=index[k];compute[i['layer_id'],i['stage'],i['rank_id']]=dict(start_ns=n['start_ns'],end_ns=n['end_ns'])
            if (n['graph_id'],n['rank_id']) in plans:selected[k]=n
        endpoints={(r['rank_id'],r['local_record_id']):r for r in iter_result(ex,'endpoint_ops',report)}
        operations={}
        for op in iter_result(ex,'operation_registry',report):
            members={r['rank_id']:endpoints[r['rank_id'],r['local_record_id']] for r in op['endpoint_records']}
            operations[op['layer_id'],op['kind']]=members
        latest_ranks={lid:{rank for rank,r in members.items() if r['end_local_ns']==max(x['end_local_ns'] for x in members.values())}
                      for (lid,kind),members in operations.items() if kind=='combine'}
        op_layers={o['operation_id']:o['layer_id'] for o in iter_result(ex,'operation_registry',report)}
        incoming={}
        for t in iter_result(ex,'planned_transfers',report):
            lid=op_layers[t['operation_id']]
            if t['kind']=='combine' and t['message_role']=='data' and t['dst_rank'] in latest_ranks[lid]:
                incoming[t['transfer_id']]={**t,'layer_id':lid}
        transfers={t['transfer_id']:t for t in iter_result(ex,'transfers',report) if t['transfer_id'] in ids or t['transfer_id'] in incoming}
        ports=defaultdict(Counter)
        for p in read(run/'observed/public_config.json')['ports']:ports[p['switch_id'],p['port_id']]
        for r in stored_rows(run/'observed','port_windows.csv'):
            ports[r['switch_id'],r['port_id']].update(packets=r['counted_packets'],packet_bytes=r['counted_packet_bytes'])
        for r in stored_rows(run/'truth','message_attribution_windows.csv'):
            ports[r['switch_id'],r['port_id']][r['message_category']]+=r['application_bytes']
        return report,compute,selected,operations,transfers,ports,incoming
    a,ac,an,ao,at,ap,ai=describe(left);b,bc,bn,bo,bt,bp,bi=describe(right)
    assert a['random']==b['random'] and set(ac)==set(bc) and set(ao)==set(bo) and set(ap)==set(bp)
    before=row_digest(iter_result(left/'raw/execution','planned_transfers'))
    after=row_digest(iter_result(right/'raw/execution','planned_transfers'));assert before==after
    assert all(ac[k]['end_ns']-ac[k]['start_ns']==bc[k]['end_ns']-bc[k]['start_ns'] for k in ac)
    layers=[]
    for target in initial['chains']:
        lid,source,consumer=target['layer_id'],target['source_rank'],target['consumer_rank'];tid=target['key_transfer_id']
        def layer(compute,nodes,ops,transfers,incoming):
            members=ops[lid,'combine'];latest=max(r['end_local_ns'] for r in members.values())
            start=compute[lid,'routed_expert',source]['end_ns'];end=transfers[tid]['send_start_ns']
            graph_id,_,expert_id=next(k for k,v in index.items() if
                (v['layer_id'],v['stage'],v['rank_id'])==(lid,'routed_expert',source))
            plan=plans[graph_id,source]
            send_id=next(i for i,n in plan.items() if n['kind']=='SEND' and n['transfer_id']==tid)
            dependency_end=max(nodes[graph_id,source,d]['end_ns'] for d in plan[send_id]['deps'])
            submit_ties=sorted(d for d in plan[send_id]['deps'] if nodes[graph_id,source,d]['end_ns']==dependency_end)
            assert nodes[graph_id,source,send_id]['start_ns']==end==dependency_end
            chain,durations=predecessor_chain(plan,{i:nodes[graph_id,source,i] for i in plan},submit_ties[0],expert_id)
            assert sum(durations.values())==end-start
            following={f'{layer}.{stage}':compute[layer,stage,consumer] for layer,stage in
                [(lid,'shared_routed_add'),(lid,'ffn_residual'),(lid+1,'attention_norm'),(lid+1,'router')]
                if (layer,stage,consumer) in compute}
            last_consumers=sorted(r for r in members if members[r]['end_local_ns']==latest)
            assert last_consumers and latest>=members[consumer]['end_local_ns']
            arrivals=[]
            for rank in last_consumers:
                eligible=[t for i,t in incoming.items() if i in transfers and t['dst_rank']==rank and t['layer_id']==lid]
                last=max((transfers[t['transfer_id']]['data_ready_ns'] for t in eligible),default=None)
                assert last is not None, 'Missing actual last consumer data contributions'
                arrivals.extend(dict(consumer_rank=rank,source_rank=t['src_rank'],transfer_id=t['transfer_id'],
                    data_ready_ns=last,business_identity={k:t[k] for k in ('message_id','chunk_index','payload_bytes','pair_byte_range')})
                    for t in eligible if transfers[t['transfer_id']]['data_ready_ns']==last)
            return dict(source_expert_end_ns=start,same_data_submit_ns=end,expert_to_data_submit_ns=end-start,
                same_data_ready_ns=transfers[tid]['data_ready_ns'],nonoverlapping_components_ns=dict(durations),
                actual_predecessor_chain=chain,
                submit_dependency_ties=submit_ties,
                original_consumer_combine_end_ns=members[consumer]['end_local_ns'],last_combine_end_ns=latest,
                actual_last_consumers=last_consumers,latest_incoming_data_for_actual_last_consumers=arrivals,
                original_consumer_following_compute=following,
                layer_compute_end_ns=max(v['end_ns'] for k,v in compute.items() if k[:2]==(lid,'ffn_residual')))
        layers.append(dict(layer_id=lid,source_rank=source,original_consumer_rank=consumer,key_transfer_id=tid,
                           baseline=layer(ac,an,ao,at,ai),control=layer(bc,bn,bo,bt,bi)))
    # Check request byte ranges from the actual selected-endpoint captures.
    def requests(rows):
        return sorted((r['rank_id'],r['affected']['identity']['transfer_id'],r['affected']['thread_id'],
                       r['affected']['offset'],r['affected']['bytes']) for r in rows if r['event']=='request_issued')
    inventory=read(str(baseline_capture)+'.json');assert digest(baseline_capture)==inventory['archive_sha256']
    member='run/raw/execution/records/native_queue.jsonl.gz'
    with tarfile.open(baseline_capture,'r|gz') as stream:
        for item in stream:
            if item.name==member:
                data=stream.extractfile(item).read();assert hashlib.sha256(data).hexdigest()==inventory['entries'][member]['sha256']
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
                    original_requests=requests(json.loads(line) for line in source)
                break
        else:raise ValueError('Missing archived native queue')
    changed_requests=requests(checked_rows(right/'raw/execution',b));assert original_requests==changed_requests
    effective=read(right/'raw/execution/effective_backend.json')
    for key,value in {**selection['control']['fixed_attributes'],selection['control']['attribute']:'64'}.items():
        assert effective['attributes'][key]==value
        objects=[o for o in effective['objects'] if key in o['attributes']]
        assert objects and all(o['attributes'][key]==value for o in objects)
    save(output/'effect.json',dict(schema_version='issue-screening-native-quota-effect/1',passed=True,
        selection=selection,baseline_capture_sha256=inventory['archive_sha256'],planned_transfer_digest=before,
        same_compute_durations=True,selected_native_request_ranges_identical=True,selected_requests=len(changed_requests),
        layers=layers,baseline_simulation_end_ns=a['simulation_end_ns'],control_simulation_end_ns=b['simulation_end_ns'],
        modeled_end_reduction_percent=100*(a['simulation_end_ns']-b['simulation_end_ns'])/a['simulation_end_ns'],
        physical_packets=dict(baseline=a['network_drain'],control=b['network_drain']),
        port_totals=dict(baseline=dict(sum(ap.values(),Counter())),control=dict(sum(bp.values(),Counter()))),
        ports_with_changed_packets=sum(ap[k]['packets']!=bp[k]['packets'] for k in ap),
        ports_with_changed_wire_bytes=sum(ap[k]['packet_bytes']!=bp[k]['packet_bytes'] for k in ap),
        ports_with_changed_business_byte_hops=sum(ap[k]['business_data']!=bp[k]['business_data'] for k in ap),
        path_scope='All observed egress totals and actual application-byte attribution; no paired packet-path equality claim',
        program_sha256=digest(__file__)))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('prepare','run','compare','analyze','effect'))
    p.add_argument('--case',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--backend',type=Path);p.add_argument('--platform-commit')
    p.add_argument('--store-outstanding',type=int,choices=(32,64),default=32)
    p.add_argument('--baseline-capture',type=Path,help='effect: verified quota-32 capture archive')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.action=='run':cli(a.output,'replay','replay','--slice',a.case/'slice','--config',a.output/'replay.json','--output',a.output/'run')
    elif a.worker:
        if a.action=='prepare':prepare(a.case,a.output,a.backend,a.platform_commit,a.store_outstanding)
        elif a.action=='compare':
            from .path_replay import equivalent
            equivalent(a.case,a.output)
        elif a.action=='analyze':analyze(a.case,a.output)
        else:effect(a.case,a.output,a.baseline_capture)
    else:
        from moe_ub_cosim.host_probe import probe
        r=probe([sys.executable,'-m',__spec__.name,*sys.argv[1:],'--worker'],str(a.output)+'-'+a.action+'-resources',
                **host(extra_roots=[a.case,a.output]))
        if r['status']!='completed':raise SystemExit('Native '+a.action+' failed: '+r['status'])


if __name__=='__main__':main()
