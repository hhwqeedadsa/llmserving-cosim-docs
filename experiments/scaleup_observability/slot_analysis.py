"""Researcher-only paired execution and reconstructed SEND-slot occupancy."""
from bisect import bisect_right
from collections import defaultdict
import csv
from pathlib import Path
import sys

from moe_ub_cosim.record_storage import iter_result

from .common import digest, read, require, save, sources
from .inputs import SEEDS, verified_case
from .overview import table
from .slots import verify_pair_inputs, verify_reference
from .statistics import distribution
from .workflow import verify_complete


def occupancy_intervals(nodes, slots, end):
    """Positive-duration SEND intervals; no claim about within-nanosecond order."""
    require(slots in (1,2) and end>0, 'Invalid occupancy bounds')
    rows=[]
    for rank in sorted({n['rank_id'] for n in nodes}):
        changes=defaultdict(int,{0:0,end:0})
        for n in nodes:
            if n['rank_id']!=rank or n['kind']!='SEND':continue
            a,b=n['start_ns'],n['end_ns']
            require(0<=a<=b<=end, 'SEND outside execution interval')
            if a<b:
                changes[a]+=1;changes[b]-=1
        points=sorted(changes);active=0
        for a,b in zip(points,points[1:]):
            active+=changes[a]
            require(0<=active<=slots, 'SEND occupancy exceeds configured slots')
            rows.append(dict(rank_id=rank,start_ns=a,end_ns=b,active_sends=active,free_slots=slots-active))
        require(active+changes[points[-1]]==0, 'SEND slots did not drain')
    return rows


def wait_rows(nodes, occupancy):
    by_rank=defaultdict(list)
    for row in occupancy:by_rank[row['rank_id']].append(row)
    ends={r:[p['end_ns'] for p in rows] for r,rows in by_rank.items()}
    rows=[]
    for node in nodes:
        if node['kind']!='SEND':continue
        rank=node['rank_id'];ready,start=node['dependency_ready_ns'],node['start_ns']
        require(0<=ready<=start, 'Invalid dependency-ready interval')
        full=free=idle=0
        for p in by_rank[rank][bisect_right(ends[rank],ready):]:
            if p['start_ns']>=start:break
            duration=max(0,min(start,p['end_ns'])-max(ready,p['start_ns']))
            if p['free_slots']==0:full+=duration
            else:free+=duration
            if p['active_sends']==0:idle+=duration
        require(full+free==start-ready, 'Occupancy does not cover waiting interval')
        rows.append(dict(rank_id=rank,node_id=node['node_id'],layer_id=node['layer_id'],
            dependency_ready_ns=ready,start_ns=start,end_ns=node['end_ns'],
            wait_ns=start-ready,full_slot_ns=full,free_slot_ns=free,zero_active_ns=idle))
    return rows


def csv_rows(path, integers=()):
    with Path(path).open() as stream:rows=list(csv.DictReader(stream))
    for row in rows:
        for key in integers:
            if row[key]!='':row[key]=int(row[key])
    return rows


def graph(run):
    state=read(run/'raw/service_result.json')
    require(len(state['waves'])==1,'Need one Decode wave')
    name=state['waves'][0]['graph_id']
    require('/' not in name and '..' not in name,'Invalid graph id')
    return read(run/'raw/graphs'/name/'plan.json')


def analyze_case(root, slots, output):
    overview=root/'overview';run=root/'run'
    summary=read(overview/'summary.json');plan=graph(run)
    require(plan['send_slots_per_rank']==slots,'Executed send-slot configuration differs')
    nodes=csv_rows(overview/'nodes.csv',('rank_id','node_id','dependency_ready_ns','start_ns','end_ns','duration_ns','after_dependencies_before_start_ns'))
    timing={(n['rank_id'],n['node_id']):n for n in nodes}
    planned={t['transfer_id']:t for t in iter_result(run/'raw/execution','planned_transfers')}
    actual={t['transfer_id']:t for t in iter_result(run/'raw/execution','transfers')}
    local={t['transfer_id']:t for t in iter_result(run/'raw/execution','local_deliveries')}
    require(set(planned)==set(actual)|set(local) and not set(actual)&set(local),'Transfer coverage mismatch')
    mapping={}
    for t in planned.values():
        key=(t['src_rank'],t['send_node_id']);node=timing[key]
        require(node['kind']=='SEND' and key not in mapping,'Invalid SEND/transfer association')
        if t['transfer_id'] in actual:
            event=actual[t['transfer_id']]
            require(node['start_ns']==event['send_start_ns'] and node['end_ns']==event['send_done_ns'],
                    'SEND occupancy does not match actual submit/release')
            operation=event['operation']
        else:
            event=local[t['transfer_id']]
            require(node['start_ns']==node['end_ns']==event['send_callback_ns'],'Nonzero local SEND occupancy')
            operation='local'
        mapping[key]=dict(transfer_id=t['transfer_id'],kind=t['kind'],message_role=t['message_role'],
                         operation=operation,payload_bytes=t['payload_bytes'])
    require(set(mapping)=={key for key,n in timing.items() if n['kind']=='SEND'},'Unmatched SEND nodes')
    occupancy=occupancy_intervals(nodes,slots,summary['round_ns'])
    waits=wait_rows(nodes,occupancy)
    output.mkdir(parents=True)
    table(output/'slot-occupancy.csv',occupancy)
    table(output/'send-waits.csv',waits)
    plans={(g['rank_id'],n['node_id']):n for g in plan['rank_graphs'] for n in g['nodes']}
    examples=[]
    for w in sorted(waits,key=lambda r:(-r['wait_ns'],r['rank_id'],r['node_id']))[:5]:
        key=(w['rank_id'],w['node_id'])
        holders=[]
        releases=[]
        for n in nodes:
            if n['kind']!='SEND' or n['rank_id']!=w['rank_id'] or n['node_id']==w['node_id']:continue
            duration=max(0,min(w['start_ns'],n['end_ns'])-max(w['dependency_ready_ns'],n['start_ns']))
            detail=dict(node_id=n['node_id'],start_ns=n['start_ns'],release_ns=n['end_ns'],
                        **mapping[n['rank_id'],n['node_id']])
            if duration>0:holders.append(dict(**detail,overlap_ns=duration))
            if n['start_ns']<n['end_ns']==w['start_ns']:releases.append(detail)
        examples.append(dict(**w,**mapping[key],
            explicit_predecessors=[dict(node_id=d,kind=timing[w['rank_id'],d]['kind'],
                end_ns=timing[w['rank_id'],d]['end_ns']) for d in plans[key]['deps']],
            overlapping_sends=sorted(holders,key=lambda r:(r['start_ns'],r['node_id'])),
            positive_duration_sends_released_at_start=releases))
    save(output/'longest-waits.json',dict(selection='Five longest SEND waits per execution; ties rank_id then node_id',
        access='researcher_internal',examples=examples,
        note='Reconstructed interval occupancy and same-time releases; not a direct scheduler-ready trace or complete critical path.'))
    ports=csv_rows(overview/'ports.csv')
    groups=csv_rows(overview/'transfer-groups.csv')
    selected=[g for g in groups if g['message_role']=='data' and g['locality']=='cross_server']
    stats=dict(send_slots_per_rank=slots,round_ns=summary['round_ns'],
        single_round_tokens_per_second=summary['decode_tokens_per_second'],
        request_count=summary['request_count'],compute_work_rank_ns=summary['compute_work_ns'],
        network=summary['network'],max_port_busy_fraction=max(float(p['link_busy_fraction']) for p in ports),
        total_physical_busy_port_ns=sum(int(p['link_busy_ns']) for p in ports),
        max_voq_bytes=max(int(p['voq_peak_bytes']) for p in ports),max_fifo_bytes=max(int(p['fifo_peak_bytes']) for p in ports),
        send_nodes=len(waits),send_wait_ns=distribution([w['wait_ns'] for w in waits]),
        send_wait_total_node_ns=sum(w['wait_ns'] for w in waits),
        wait_with_full_slots_node_ns=sum(w['full_slot_ns'] for w in waits),
        wait_with_free_slot_node_ns=sum(w['free_slot_ns'] for w in waits),
        full_occupancy_rank_ns=sum(p['end_ns']-p['start_ns'] for p in occupancy if p['free_slots']==0),
        reconstructed_peak_active_sends=max(p['active_sends'] for p in occupancy),
        cross_server_data_groups=selected,
        longest_wait=examples[0],
        evidence=dict(overview_sha256=digest(overview/'summary.json'),plan_sha256=digest(run/'raw/graphs'/plan['graph_id']/'plan.json'),
            tables={p.name:digest(p) for p in sorted(output.iterdir()) if p.is_file()}))
    save(output/'summary.json',stats)
    return stats,nodes,waits,plan


def compare(batch, output):
    batch,output=Path(batch).resolve(),Path(output).resolve()
    require(output.is_dir() and not (output/'comparison.json').exists(),'Need new comparison output')
    metadata=read(batch/'batch.json')
    require(metadata['schema_version']=='scaleup-slot-batch/1','Need slot comparison batch')
    cases=metadata['cases']
    require(len(cases)==3 and {(c['input_set'],c['requests_per_rank'],c['send_slots_per_rank']) for c in cases}==
            {(s,8,2) for s in SEEDS},'Need three distinct fixed input pairs')
    pairs=[];all_platform=[];all_backend=set();all_core=[]
    for case in cases:
        _,folder=verified_case(batch,case['case_id'])
        base,base_folder,base_done,base_summary=verify_reference(case['baseline'])
        verify_pair_inputs(base_folder,folder)
        done,summary=verify_complete(batch,case)
        roots=[Path(case['baseline']['batch'])/'runs'/base['case_id'],batch/'runs'/case['case_id']]
        for root,d,s in zip(roots,(base_done,done),(base_summary,summary)):
            all_backend.add(d['backend_sha256'])
            all_platform.append(read(root/'run/manifest.json')['software']['source_hashes'])
            all_core.append({k:v for k,v in s['implementation'].items() if k.endswith(('/overview.py','/statistics.py'))})
        for key in ('request_count','compute_work_ns','completed_decode_tokens','route_records_consumed','input_expert_counts','raw_native_sizes'):
            require(base_summary[key]==summary[key], f'Paired executed workload differs: {key}')
        for key in ('fabric_tasks','local_deliveries','unique_fabric_business_bytes','tasks_with_multiple_committed_segments'):
            require(base_summary['transfers'][key]==summary['transfers'][key], f'Paired executed communication differs: {key}')
        pairdir=output/f's{case["input_set"]}'
        old,old_nodes,old_waits,old_plan=analyze_case(roots[0],1,pairdir/'slots1')
        new,new_nodes,new_waits,new_plan=analyze_case(roots[1],2,pairdir/'slots2')
        for key in ('rank_graphs','transfers','operations','full_model_profile','expert_input_counts'):
            require(old_plan[key]==new_plan[key], f'Paired plan differs: {key}')
        before={(n['rank_id'],n['node_id']):n for n in old_nodes}
        after={(n['rank_id'],n['node_id']):n for n in new_nodes}
        require(set(before)==set(after),'Paired node identities differ')
        differences=[]
        for key,a in before.items():
            b=after[key]
            require(a['kind']==b['kind'],'Paired node kinds differ')
            differences.append(dict(rank_id=key[0],node_id=key[1],kind=a['kind'],layer_id=a['layer_id'],
                before_start_ns=a['start_ns'],after_start_ns=b['start_ns'],
                start_delta_ns=b['start_ns']-a['start_ns'],end_delta_ns=b['end_ns']-a['end_ns'],
                dependency_ready_delta_ns=b['dependency_ready_ns']-a['dependency_ready_ns'],
                wait_delta_ns=b['after_dependencies_before_start_ns']-a['after_dependencies_before_start_ns']))
        table(pairdir/'matched-nodes.csv',differences)
        old_wait={(n['rank_id'],n['node_id']):n for n in old_waits}
        new_wait={(n['rank_id'],n['node_id']):n for n in new_waits}
        top=[(n['rank_id'],n['node_id']) for n in sorted(old_waits,key=lambda r:(-r['wait_ns'],r['rank_id'],r['node_id']))[:5]]
        pairs.append(dict(input_set=case['input_set'],baseline_case=base['case_id'],treatment_case=case['case_id'],
            baseline=old,treatment=new,round_delta_ns=new['round_ns']-old['round_ns'],
            round_reduction_fraction=(old['round_ns']-new['round_ns'])/old['round_ns'],
            matched_longest_baseline_waits=[dict(rank_id=k[0],node_id=k[1],before=old_wait[k],after=new_wait[k]) for k in top],
            matched_nodes_sha256=digest(pairdir/'matched-nodes.csv')))
    require(len(all_backend)==1 and all(p==all_platform[0] for p in all_platform) and
            all(p==all_core[0] for p in all_core),'Paired source, backend or overview implementation differs')
    result=dict(schema_version='scaleup-slot-comparison/1',access='researcher_internal',
        batch_sha256=digest(batch/'batch.json'),backend_sha256=next(iter(all_backend)),implementation=sources(),
        pairs=pairs,checks=dict(three_fixed_pairs=True,input_only_slots_changed=True,plans_and_workload_equal=True,
            same_platform_and_overview=True,send_intervals_match_submit_release=True,occupancy_within_slot_limit=True),
        qualifications=['Reference compute and synthetic routing; no hardware calibration.',
            'Slot-full time reconstructed from SEND lifetimes; scheduler-ready reason is not directly logged.',
            'Summed node waits and rank occupancy durations are not whole-round losses.',
            'Same-time callbacks lack sub-nanosecond ordering; no complete critical-path claim.'])
    save(output/'comparison.json',result)
    print('Three paired inputs, unchanged work, slot occupancy and completion comparisons verified',flush=True)


if __name__=='__main__':compare(*sys.argv[1:])
